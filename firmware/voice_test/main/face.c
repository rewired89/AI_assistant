#include "face.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "board_waveshare_esp32s3_lcd154.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "esp_attr.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "nt_face";
#define W 240
#define H 240
#define STRIP 20
#define BACKLIGHT_PERCENT 30   // firmware-owned limit
#define ANIM_MS 2500

enum { EYE_NORMAL, EYE_ARCH, EYE_ANGRY, EYE_SAD };
enum { MOUTH_NONE, MOUTH_SMILE, MOUTH_LINE, MOUTH_OPEN, MOUTH_FROWN, MOUTH_TALK };
typedef struct { const char *name; uint8_t eye, rx, ry, mouth; int8_t dx, dy; bool cheeks, gray; } look_t;

static const look_t LOOKS[] = {
    {"idle", EYE_NORMAL, 20, 30, MOUTH_SMILE, 0, 0, false, false},
    {"calm", EYE_NORMAL, 20, 28, MOUTH_LINE, 0, 0, false, false},
    {"happy", EYE_ARCH, 24, 26, MOUTH_SMILE, 0, 0, false, false},
    {"excited", EYE_NORMAL, 24, 34, MOUTH_OPEN, 0, -4, false, false},
    {"celebratory", EYE_ARCH, 24, 26, MOUTH_OPEN, 0, 0, true, false},
    {"affectionate", EYE_ARCH, 22, 24, MOUTH_SMILE, 0, 0, true, false},
    {"playing", EYE_NORMAL, 20, 30, MOUTH_OPEN, 0, 0, false, false},
    {"curious", EYE_NORMAL, 22, 32, MOUTH_LINE, 8, -8, false, false},
    {"confused", EYE_NORMAL, 18, 24, MOUTH_LINE, -6, 0, false, false},
    {"surprised", EYE_NORMAL, 26, 34, MOUTH_OPEN, 0, -6, false, false},
    {"thinking", EYE_NORMAL, 18, 26, MOUTH_LINE, 12, -12, false, false},
    {"listening", EYE_NORMAL, 22, 34, MOUTH_NONE, 0, 0, false, false},
    {"talking", EYE_NORMAL, 20, 30, MOUTH_TALK, 0, 0, false, false},
    {"eating", EYE_ARCH, 22, 24, MOUTH_TALK, 0, 0, false, false},
    {"sad", EYE_SAD, 20, 28, MOUTH_FROWN, 0, 6, false, false},
    {"angry", EYE_ANGRY, 22, 28, MOUTH_LINE, 0, 0, false, false},
    {"sleepy", EYE_NORMAL, 22, 9, MOUTH_NONE, 0, 8, false, false},
    {"sleeping", EYE_NORMAL, 22, 3, MOUTH_NONE, 0, 10, false, false},
    {"waking", EYE_NORMAL, 22, 15, MOUTH_NONE, 0, 6, false, false},
    {"reflective", EYE_NORMAL, 20, 12, MOUTH_SMILE, 0, 10, false, false},
    {"offline", EYE_NORMAL, 20, 3, MOUTH_NONE, 0, 6, false, true},
};
#define NLOOKS ((int)(sizeof LOOKS / sizeof LOOKS[0]))

static esp_lcd_panel_handle_t g_panel;
static uint16_t *g_buf;
static bool g_ok;
static face_finished_cb g_cb;
static SemaphoreHandle_t g_mu;
static SemaphoreHandle_t g_done;   // given when the panel has finished reading the strip buffer (SPI DMA runs after draw_bitmap returns)
// animation state, guarded by g_mu
static int g_look = 0;                 // index into LOOKS
static bool g_active, g_loop;
static int g_prio;
static char g_cmd[65];
static int64_t g_ends_us;


static bool IRAM_ATTR strip_done(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *ev, void *ctx)
{
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(g_done, &woken);
    return woken == pdTRUE;
}

// Sends one strip and waits until the buffer may be reused. Without the wait the next strip overwrites data still being sent (horizontal glitch lines).
static void push_strip(int y0)
{
    if (esp_lcd_panel_draw_bitmap(g_panel, 0, y0, W, y0 + STRIP, g_buf) == ESP_OK) xSemaphoreTake(g_done, pdMS_TO_TICKS(500));
}

static uint16_t c565(uint8_t r, uint8_t g, uint8_t b)
{
    uint16_t v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
    return (uint16_t)((v >> 8) | (v << 8));   // the panel expects the high byte first
}


// ---------------------------------------------------------------- microphone indicator (top right corner)
static volatile int g_mic = -1;
static const char *MIC_GLYPH[14] = {
    "...XXXXXX...", "..XXXXXXXX..", "..XXXXXXXX..", "..XXXXXXXX..", "..XXXXXXXX..", "..XXXXXXXX..", "X.XXXXXXXX.X",
    "X..XXXXXX..X", ".X........X.", "..XX....XX..", "....XXXX....", ".....XX.....", ".....XX.....", "...XXXXXX...",
};
#define MIC_X 204
#define MIC_Y 8
// returns true and the colour when this screen pixel belongs to the indicator
static bool mic_px(int x, int y, uint16_t *col)
{
    int m = g_mic;
    if (m < 0) return false;
    int gx = (x - MIC_X) / 2, gy = (y - MIC_Y) / 2;
    if (x < MIC_X || y < MIC_Y || gx > 11 || gy > 13) return false;
    if (m == 0 && abs(gx * 13 - gy * 11) <= 14) { *col = c565(244, 139, 182); return true; }   // the slash: muted
    if (MIC_GLYPH[gy][gx] != 'X') return false;
    *col = m ? c565(235, 242, 246) : c565(110, 120, 128);
    return true;
}
void face_set_mic(int state) { g_mic = state; }

static int find_look(const char *name)
{
    for (int i = 0; i < NLOOKS; i++) if (strcmp(LOOKS[i].name, name) == 0) return i;
    return -1;
}
bool face_known(const char *name) { return name && find_look(name) >= 0; }

static inline bool in_ell(int dx, int dy, int rx, int ry)
{
    if (rx < 1 || ry < 1) return false;
    return dx * dx * ry * ry + dy * dy * rx * rx <= rx * rx * ry * ry;
}

typedef struct { const look_t *l; int ry; int mouth_ry; } frame_t;

static bool eye_px(const frame_t *f, int x, int y, int cx, bool left)
{
    const look_t *l = f->l;
    int ex = x - cx - l->dx, ey = y - (100 + l->dy);
    int rx = l->rx, ry = f->ry;
    if (l->eye == EYE_ARCH) return in_ell(ex, ey, rx, ry) && !in_ell(ex, ey, rx - 8, ry - 8) && ey <= 6;
    if (!in_ell(ex, ey, rx, ry)) return false;
    int top = ey + ry, side = left ? ex + rx : rx - ex;   // distance from top edge, from the outer edge
    if (l->eye == EYE_ANGRY && top * 2 < side) return false;
    if (l->eye == EYE_SAD && top * 2 < (2 * rx - side)) return false;
    return true;
}

static uint16_t px(const frame_t *f, int x, int y)
{
    static const uint16_t BG = 0, WHITE = 0xFFFF;
    const look_t *l = f->l;
    uint16_t eye = l->gray ? c565(110, 120, 128) : c565(235, 242, 246), pink = c565(244, 139, 182);
    (void)WHITE;
    if (eye_px(f, x, y, 80, true) || eye_px(f, x, y, 160, false)) return eye;
    int mx = x - 120, my = y - 180;
    switch (l->mouth) {
    case MOUTH_SMILE: if (in_ell(mx, my, 24, 12) && !in_ell(mx, my, 24 - 5, 12 - 5) && my >= 0) return pink; break;
    case MOUTH_FROWN: if (in_ell(mx, my + 8, 24, 12) && !in_ell(mx, my + 8, 24 - 5, 12 - 5) && my + 8 <= 0) return pink; break;
    case MOUTH_LINE: if (mx > -16 && mx < 16 && my >= -2 && my <= 2) return pink; break;
    case MOUTH_OPEN: if (in_ell(mx, my, 14, 12)) return pink; break;
    case MOUTH_TALK: if (in_ell(mx, my, 16, f->mouth_ry)) return pink; break;
    default: break;
    }
    if (l->cheeks && (in_ell(x - 40, y - 150, 14, 8) || in_ell(x - 200, y - 150, 14, 8))) return pink;
    return BG;
}

static void draw(const frame_t *f)
{
    for (int y0 = 0; y0 < H; y0 += STRIP) {
        for (int y = 0; y < STRIP; y++)
            for (int x = 0; x < W; x++) {
                uint16_t col = px(f, x, y0 + y), mc;
                if (mic_px(x, y0 + y, &mc)) col = mc;
                g_buf[y * W + x] = col;
            }
        push_strip(y0);
    }
}


// ---------------------------------------------------------------- sprites (NTSB bundle of NTSP sets, memory-mapped from flash)
#define MAX_SETS 3
#define MAX_ANIMS 32
#define SPRITE_SCALE 4
typedef struct { char name[24]; uint8_t loop, nframes; const uint8_t *frames; } sanim_t;
typedef struct {
    char name[9];
    int w, h, ncol, nanim;
    uint16_t pal[64];
    bool opaque[64];
    sanim_t anim[MAX_ANIMS];
} sset_t;
static sset_t g_sets[MAX_SETS];
static int g_nsets;
static volatile int g_active_set = -1;

static bool parse_set(sset_t *s, const uint8_t *d, size_t size)
{
    if (size < 9 || memcmp(d, "NTSP", 4) != 0 || d[4] != 1) return false;
    s->w = d[5]; s->h = d[6]; s->ncol = d[7]; s->nanim = 0;
    int na = d[8];
    if (s->w < 1 || s->h < 1 || s->ncol < 1 || s->ncol > 64) return false;
    size_t p = 9;
    if (p + (size_t)s->ncol * 4 > size) return false;
    for (int i = 0; i < s->ncol; i++, p += 4) {
        s->pal[i] = c565(d[p], d[p + 1], d[p + 2]);
        s->opaque[i] = d[p + 3] >= 128;
    }
    size_t stride = 2 + (size_t)s->w * s->h;
    for (int a = 0; a < na; a++) {
        if (p >= size) return false;
        int nl = d[p++];
        if (p + nl + 2 > size) return false;
        sanim_t tmp = {0};
        if (nl < (int)sizeof tmp.name) { memcpy(tmp.name, d + p, nl); tmp.name[nl] = 0; }
        p += nl;
        tmp.loop = d[p]; tmp.nframes = d[p + 1]; p += 2;
        tmp.frames = d + p;
        if (p + (size_t)tmp.nframes * stride > size) return false;
        p += (size_t)tmp.nframes * stride;
        if (tmp.name[0] && tmp.nframes > 0 && s->nanim < MAX_ANIMS) s->anim[s->nanim++] = tmp;
    }
    return true;
}

static void sprites_load(void)
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, (esp_partition_subtype_t)0x40, "sprites");
    if (!part) { ESP_LOGW(TAG, "no sprites partition: built-in face only"); return; }
    const void *ptr = NULL;
    esp_partition_mmap_handle_t hnd;
    if (esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &ptr, &hnd) != ESP_OK) { ESP_LOGW(TAG, "sprites partition not mappable"); return; }
    const uint8_t *d = ptr;
    if (memcmp(d, "NTSB", 4) != 0 || d[4] != 1) { ESP_LOGW(TAG, "sprites partition is empty (flash build/sprites.bin): built-in face only"); return; }
    int n = d[5];
    for (int i = 0; i < n && g_nsets < MAX_SETS; i++) {
        const uint8_t *e = d + 6 + 16 * i;
        uint32_t off, size;
        memcpy(&off, e + 8, 4);
        memcpy(&size, e + 12, 4);
        if ((size_t)off + size > part->size) continue;
        sset_t *s = &g_sets[g_nsets];
        memset(s, 0, sizeof *s);
        memcpy(s->name, e, 8);
        s->name[8] = 0;
        if (parse_set(s, d + off, size)) {
            ESP_LOGI(TAG, "sprite set '%s': %dx%d, %d animations", s->name, s->w, s->h, s->nanim);
            g_nsets++;
        }
    }
}

bool face_set_sprites(const char *set)
{
    for (int i = 0; set && i < g_nsets; i++)
        if (strcmp(g_sets[i].name, set) == 0) { g_active_set = i; ESP_LOGI(TAG, "sprite set: %s", set); return true; }
    return false;
}

static const sanim_t *sprite_anim(const sset_t *s, const char *name)
{
    for (int i = 0; i < s->nanim; i++) if (strcmp(s->anim[i].name, name) == 0) return &s->anim[i];
    return NULL;
}

// which frame is showing t_ms after the animation started
static int sprite_frame(const sset_t *s, const sanim_t *a, int t_ms)
{
    size_t stride = 2 + (size_t)s->w * s->h;
    int total = 0;
    for (int i = 0; i < a->nframes; i++) {
        uint16_t ms;
        memcpy(&ms, a->frames + i * stride, 2);
        total += ms ? ms : 100;
    }
    if (total <= 0) return 0;
    if (a->loop) t_ms %= total;
    int acc = 0;
    for (int i = 0; i < a->nframes; i++) {
        uint16_t ms;
        memcpy(&ms, a->frames + i * stride, 2);
        acc += ms ? ms : 100;
        if (t_ms < acc) return i;
    }
    return a->nframes - 1;
}

static void draw_sprite(const sset_t *s, const sanim_t *a, int frame)
{
    size_t stride = 2 + (size_t)s->w * s->h;
    const uint8_t *px8 = a->frames + frame * stride + 2;
    int sw = s->w * SPRITE_SCALE, sh = s->h * SPRITE_SCALE, ox = (W - sw) / 2, oy = (H - sh) / 2;
    for (int y0 = 0; y0 < H; y0 += STRIP) {
        for (int y = 0; y < STRIP; y++) {
            int yy = y0 + y - oy;
            for (int x = 0; x < W; x++) {
                int xx = x - ox;
                uint16_t col = 0;
                if (yy >= 0 && yy < sh && xx >= 0 && xx < sw) {
                    uint8_t idx = px8[(yy / SPRITE_SCALE) * s->w + (xx / SPRITE_SCALE)];
                    if (idx < s->ncol && s->opaque[idx]) col = s->pal[idx];
                }
                uint16_t mc;
                if (mic_px(x, y0 + y, &mc)) col = mc;
                g_buf[y * W + x] = col;
            }
        }
        push_strip(y0);
    }
}


// ---------------------------------------------------------------- setup code (big digits, 3x5 font)
static char g_code[12];
static const uint8_t DIGIT_FONT[10][5] = {   // 3 bits per row, left bit = left pixel
    {7, 5, 5, 5, 7}, {2, 6, 2, 2, 7}, {7, 1, 7, 4, 7}, {7, 1, 7, 1, 7}, {5, 5, 7, 1, 1},
    {7, 4, 7, 1, 7}, {7, 4, 7, 5, 7}, {7, 1, 1, 2, 2}, {7, 5, 7, 5, 7}, {7, 5, 7, 1, 7}};
#define CODE_SCALE 7
static bool code_px(int x, int y)
{
    int n = (int)strlen(g_code), pitch = 3 * CODE_SCALE + CODE_SCALE;               // digit width plus one cell of space
    int total = n * pitch - CODE_SCALE, x0 = (W - total) / 2, y0 = (H - 5 * CODE_SCALE) / 2;
    if (y < y0 || y >= y0 + 5 * CODE_SCALE || x < x0 || x >= x0 + total) return false;
    int i = (x - x0) / pitch, cx = (x - x0) % pitch;
    if (i >= n || cx >= 3 * CODE_SCALE || g_code[i] < '0' || g_code[i] > '9') return false;
    return (DIGIT_FONT[g_code[i] - '0'][(y - y0) / CODE_SCALE] >> (2 - cx / CODE_SCALE)) & 1;
}
static void draw_code(void)
{
    for (int y0 = 0; y0 < H; y0 += STRIP) {
        for (int y = 0; y < STRIP; y++)
            for (int x = 0; x < W; x++) g_buf[y * W + x] = code_px(x, y0 + y) ? c565(235, 242, 246) : 0;
        push_strip(y0);
    }
}
void face_show_code(const char *digits) { strlcpy(g_code, digits ? digits : "", sizeof g_code); }

static void finish_cb(const char *name, const char *cmd, bool interrupted)
{
    if (g_cb && cmd[0]) g_cb(name, cmd, interrupted);
}

static void face_task(void *arg)
{
    int last_key = -1;
    int64_t start = esp_timer_get_time();
    for (;;) {
        int64_t now = esp_timer_get_time();
        char done_name[24] = "", done_cmd[65] = "";
        xSemaphoreTake(g_mu, portMAX_DELAY);
        if (g_active && !g_loop && now >= g_ends_us) {          // a one-shot animation ran its course: return to idle
            strncpy(done_name, LOOKS[g_look].name, sizeof done_name - 1);
            strncpy(done_cmd, g_cmd, sizeof done_cmd - 1);
            g_active = false;
            g_cmd[0] = 0;
            g_look = 0;
        }
        int look = g_look;
        xSemaphoreGive(g_mu);
        if (done_cmd[0]) finish_cb(done_name, done_cmd, false);
        if (g_code[0]) {                                   // setup hotspot password on screen: nothing else is drawn
            if (last_key != -2) { draw_code(); last_key = -2; }
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }

        static int last_look = -1;
        static int64_t look_t0;
        if (look != last_look) { last_look = look; look_t0 = now; }
        int set = g_active_set;
        const sanim_t *an = NULL;
        if (set >= 0) {
            an = sprite_anim(&g_sets[set], LOOKS[look].name);
            if (!an) an = sprite_anim(&g_sets[set], "idle");   // this set has no art for that mood: show its idle
        }
        if (an) {
            int frame = sprite_frame(&g_sets[set], an, (int)((now - look_t0) / 1000));
            int skey = 1000000 + (g_mic + 1) * 10000000 + set * 100000 + (int)(an - g_sets[set].anim) * 100 + frame;
            if (skey != last_key) {
                draw_sprite(&g_sets[set], an, frame);
                last_key = skey;
            }
            vTaskDelay(pdMS_TO_TICKS(40));
            continue;
        }
        int t_ms = (int)((now - start) / 1000);
        bool blink = (t_ms % 4200) < 140;
        int talk = (t_ms / 160) % 4;                            // 0..3 mouth opening cycle
        const look_t *l = &LOOKS[look];
        frame_t f = {.l = l, .ry = (blink && l->ry > 12 && l->eye != EYE_ARCH) ? 3 : l->ry,
                     .mouth_ry = (const int[]){3, 9, 14, 9}[talk]};
        int key = (g_mic + 1) * 100000 + look * 100 + (f.ry == 3 && l->ry > 12 ? 1 : 0) + ((l->mouth == MOUTH_TALK) ? 10 + talk : 0);
        if (key != last_key) {
            draw(&f);
            last_key = key;
        }
        vTaskDelay(pdMS_TO_TICKS(60));
    }
}

bool face_init(face_finished_cb cb)
{
    g_cb = cb;
    g_mu = xSemaphoreCreateMutex();
    g_done = xSemaphoreCreateBinary();
    spi_bus_config_t bus = {.sclk_io_num = NT_LCD_SCLK, .mosi_io_num = NT_LCD_MOSI, .miso_io_num = -1, .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = W * STRIP * 2};
    if (spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO) != ESP_OK) { ESP_LOGE(TAG, "spi bus failed"); return false; }
    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_spi_config_t io_cfg = {.cs_gpio_num = NT_LCD_CS, .dc_gpio_num = NT_LCD_DC, .spi_mode = 3, .pclk_hz = 40 * 1000 * 1000, .trans_queue_depth = 10, .lcd_cmd_bits = 8, .lcd_param_bits = 8, .on_color_trans_done = strip_done, .user_ctx = NULL};
    if (esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_cfg, &io) != ESP_OK) { ESP_LOGE(TAG, "panel io failed"); return false; }
    esp_lcd_panel_dev_config_t pc = {.reset_gpio_num = NT_LCD_RST, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB, .bits_per_pixel = 16};
    if (esp_lcd_new_panel_st7789(io, &pc, &g_panel) != ESP_OK) { ESP_LOGE(TAG, "panel failed"); return false; }
    esp_lcd_panel_reset(g_panel);
    esp_lcd_panel_init(g_panel);
    esp_lcd_panel_invert_color(g_panel, true);
    esp_lcd_panel_disp_on_off(g_panel, true);
    g_buf = heap_caps_malloc(W * STRIP * 2, MALLOC_CAP_DMA);
    if (!g_buf) { ESP_LOGE(TAG, "no DMA buffer"); return false; }
    ledc_timer_config_t t = {.speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = LEDC_TIMER_10_BIT, .timer_num = LEDC_TIMER_0, .freq_hz = 5000, .clk_cfg = LEDC_AUTO_CLK};
    ledc_timer_config(&t);
    ledc_channel_config_t c = {.gpio_num = NT_LCD_BL, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_0, .timer_sel = LEDC_TIMER_0, .duty = (BACKLIGHT_PERCENT * 1023) / 100, .hpoint = 0};
    ledc_channel_config(&c);
    g_ok = true;
    sprites_load();
    face_set_sprites("adult");
    xTaskCreate(face_task, "face", 6144, NULL, 3, NULL);
    ESP_LOGI(TAG, "face ready");
    return true;
}

bool face_play(const char *name, bool loop, int priority, const char *cmd_id)
{
    int i = name ? find_look(name) : -1;
    if (!g_ok || i < 0) return false;
    char prev_name[24] = "", prev_cmd[65] = "";
    xSemaphoreTake(g_mu, portMAX_DELAY);
    if (g_active && g_prio > priority) { xSemaphoreGive(g_mu); return false; }
    if (g_active && g_cmd[0]) {
        strncpy(prev_name, LOOKS[g_look].name, sizeof prev_name - 1);
        strncpy(prev_cmd, g_cmd, sizeof prev_cmd - 1);
    }
    g_look = i;
    g_active = true;
    g_loop = loop;
    g_prio = priority;
    strncpy(g_cmd, cmd_id, sizeof g_cmd - 1);
    g_cmd[sizeof g_cmd - 1] = 0;
    g_ends_us = esp_timer_get_time() + (int64_t)ANIM_MS * 1000;
    xSemaphoreGive(g_mu);
    if (prev_cmd[0]) finish_cb(prev_name, prev_cmd, true);
    return true;
}

bool face_stop(void)
{
    char name[24] = "", cmd[65] = "";
    xSemaphoreTake(g_mu, portMAX_DELAY);
    bool had = g_active;
    if (had) {
        strncpy(name, LOOKS[g_look].name, sizeof name - 1);
        strncpy(cmd, g_cmd, sizeof cmd - 1);
        g_active = false;
        g_cmd[0] = 0;
        g_look = 0;
    }
    xSemaphoreGive(g_mu);
    if (had) finish_cb(name, cmd, true);
    return had;
}

void face_set_backlight(int percent)
{
    if (!g_ok) return;
    if (percent < 0) percent = 0;
    if (percent > BACKLIGHT_PERCENT) percent = BACKLIGHT_PERCENT;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, (percent * 1023) / 100);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}
