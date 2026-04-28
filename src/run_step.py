"""Step-by-step runner for sandbox time-limited execution."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import CONFIG_DIR, OUTPUT_DIR, load_env, load_yaml, log
from content import (
    cross_review,
    generate_draft,
    revise,
    self_critique,
    _build_user_prompt,
)
from perfection import perfection_pass, min_score
from validators import validate


def get_state_path(date, step):
    return OUTPUT_DIR / (date + "-step-" + step + ".json")


def load_state(date, step):
    p = get_state_path(date, step)
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(date, step, data):
    p = get_state_path(date, step)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log("saved: " + str(p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True,
                    choices=["draft", "critique", "revise", "review", "perfection", "finalize"])
    ap.add_argument("--date", required=True)
    args = ap.parse_args()

    env = load_env()
    OUTPUT_DIR.mkdir(exist_ok=True)
    date = args.date

    sched = load_yaml(CONFIG_DIR / "schedule.yaml")
    entry = sched[date]
    cols = load_yaml(CONFIG_DIR / "collections.yaml")
    cta = cols[entry["cta_collection"]]

    if args.step == "draft":
        draft = generate_draft(topic=entry["title"], date=date,
                                post_type=entry.get("type", "longtail"),
                                subtype=entry.get("subtype"), cta=cta,
                                hub_links=None, env=env)
        save_state(date, "draft", draft)

    elif args.step == "critique":
        draft = load_state(date, "draft")
        crit = self_critique(draft, env)
        save_state(date, "critique", crit)

    elif args.step == "revise":
        draft = load_state(date, "draft")
        crit = load_state(date, "critique")
        user_prompt = _build_user_prompt(date=date, topic=entry["title"],
                                          post_type=entry.get("type", "longtail"),
                                          subtype=entry.get("subtype"), cta=cta,
                                          hub_links=None, extra_notes=None)
        rev = revise(draft, crit, env, original_user_prompt=user_prompt)
        save_state(date, "revise", rev)

    elif args.step == "review":
        rev = load_state(date, "revise")
        polished = cross_review(rev, env)
        save_state(date, "review", polished)
        log("after cross-review min: " + str(min_score(polished)) + "/10")

    elif args.step == "perfection":
        try:
            current = load_state(date, "perfection")
        except FileNotFoundError:
            current = load_state(date, "review")
        cur_score = min_score(current)
        log("current min: " + str(cur_score) + "/10")
        if cur_score >= 10:
            log("already 10/10")
            return
        cand = perfection_pass(current, env)
        cand_score = min_score(cand)
        if cand_score >= cur_score:
            save_state(date, "perfection", cand)
            log("improved: " + str(cur_score) + " -> " + str(cand_score))
        else:
            log("score dropped - keep previous", "WARN")
            save_state(date, "perfection", current)

    elif args.step == "finalize":
        try:
            final = load_state(date, "perfection")
        except FileNotFoundError:
            final = load_state(date, "review")
        v = validate(final, post_type=entry.get("type", "longtail"))
        log("validation: " + str(len(v["violations"])) + " violations, " + str(len(v["warnings"])) + " warnings")
        for vv in v["violations"]:
            log("  V: " + vv["rule"] + ": " + str(vv["detail"]), "WARN")
        for ww in v["warnings"]:
            log("  W: " + ww["rule"] + ": " + str(ww["detail"]), "WARN")
        article_path = OUTPUT_DIR / (date + "-article.json")
        article_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        log("final saved: " + str(article_path))
        log("min score: " + str(min_score(final)) + "/10")
        from main import _write_preview
        _write_preview(final, OUTPUT_DIR / (date + "-preview.html"))
        log("preview saved")


if __name__ == "__main__":
    main()
