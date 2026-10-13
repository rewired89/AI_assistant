// The face on the 240x240 screen, drawn from simple shapes (no art files). Firmware owns brightness (fixed, low).
#pragma once
#include <stdbool.h>

// Called (from the face task or the caller's task) when an animation ends. cmd_id is the animation.play command id.
typedef void (*face_finished_cb)(const char *name, const char *cmd_id, bool interrupted);

bool face_init(face_finished_cb cb);          // false if the screen did not start; everything else is then a no-op
bool face_known(const char *name);            // is this one of the protocol animation names we can draw
// false = refused because a higher-priority animation is playing. A non-loop animation ends by itself after a short time.
bool face_play(const char *name, bool loop, int priority, const char *cmd_id);
bool face_stop(void);                         // false if nothing was playing
void face_set_backlight(int percent);     // clamped to 0..30 by the firmware; the host cannot raise it
