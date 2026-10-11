"""Check that the language model server answers, without loading this machine. The model runs on the OTHER computer
(e.g. the Minisforum); this laptop only sends one short question over the home network.

    python -m tools.check_llm --url http://192.168.1.50:11434 --model llama3.2:3b
    python -m tools.check_llm --config config/companion.yaml

Prints: is the server reachable, which models it has, and how long a one-sentence answer takes."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from companion.conversation import LLMUnavailable, OllamaLanguageModel


def list_models(url: str, timeout: float = 5.0) -> list[str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url.rstrip("/") + "/api/tags", timeout=timeout) as r:
        return [m["name"] for m in json.loads(r.read(1_000_000)).get("models", [])]


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    ap.add_argument("--model")
    ap.add_argument("--config")
    ap.add_argument("--timeout", type=float, default=120.0)
    a = ap.parse_args(argv)
    url, model = a.url, a.model
    if a.config:
        from companion.app import AppConfig, ConfigProblem
        try:
            cfg = AppConfig.load(a.config)
        except ConfigProblem as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        url = url or cfg.llm.get("url", "http://127.0.0.1:11434")
        model = model or cfg.llm.get("model")
    if not url or not model:
        print("error: give --url and --model, or --config", file=sys.stderr)
        return 1
    try:
        llm = OllamaLanguageModel(model, base_url=url, timeout_s=a.timeout)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        names = list_models(url)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        print(f"cannot reach {url}: {type(e).__name__}. Is Ollama running there, and listening on the network "
              f"(OLLAMA_HOST=0.0.0.0)? Is a firewall blocking port 11434?", file=sys.stderr)
        return 1
    print(f"server: {url} is up, models: {', '.join(names) or '(none)'}", file=out)
    if not any(n == model or n.split(":")[0] == model for n in names):
        print(f"error: model {model!r} is not on that server; run 'ollama pull {model}' ON THE SERVER", file=sys.stderr)
        return 1
    t = time.time()
    try:
        r = llm.complete("You are a small friendly robot. Answer in one short sentence.",
                         [{"role": "user", "content": "Say hello."}])
    except LLMUnavailable as e:
        print(f"the server answered /api/tags but the chat failed: {e}", file=sys.stderr)
        return 1
    print(f"answer in {time.time() - t:.1f} s: {r['text']!r}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
