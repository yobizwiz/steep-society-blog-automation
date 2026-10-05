"""Single-day entry point. --dry-run still calls paid text generation/review APIs."""
import argparse
from datetime import date as Date
from html import escape
import sys
from utils import CONFIG_DIR, OUTPUT_DIR, ensure_dirs, load_env, load_yaml
from weekly import prepare_article, publish_prepared
from shopify_pub import public_url


def _write_preview(article, path):
    path.write_text('<!doctype html><meta charset="utf-8"><title>' + escape(article.get('title',''))
                    + '</title><h1>' + escape(article.get('title','')) + '</h1>' + article.get('body_html',''), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=Date.today().isoformat())
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--use-existing', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--scheduled', action='store_true')
    modes.add_argument('--publish-now', action='store_true')
    parser.add_argument('--scheduled-time', default='13:00')
    args = parser.parse_args()
    Date.fromisoformat(args.date)
    ensure_dirs()
    env, schedule, cols = load_env(), load_yaml(CONFIG_DIR/'schedule.yaml'), load_yaml(CONFIG_DIR/'collections.yaml')
    if args.date not in schedule: raise RuntimeError('missing_schedule: ' + args.date)
    entry = schedule[args.date]
    article = prepare_article(args.date, env, entry, cols, use_existing=args.use_existing)
    _write_preview(article, OUTPUT_DIR / f'{args.date}-preview.html')
    if args.dry_run: return 0
    mode = 'scheduled' if args.scheduled else 'publish' if args.publish_now else env.get('PUBLISH_MODE', 'draft')
    scheduled = f'{args.date}T{args.scheduled_time}:00Z' if mode == 'scheduled' else None
    created = publish_prepared(args.date, env, entry, cols, article, publish_mode=mode, scheduled_at=scheduled)
    print(public_url(created['handle'], env=env))
    return 0


if __name__ == '__main__': sys.exit(main())
