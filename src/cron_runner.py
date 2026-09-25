"""
Kassel Wohnungssuche — Cron Runner.
Orchestrates the scraper + email checker on a schedule.

Crontab examples:
  # Scrape ImmoScout24 every 4 hours
  0 */4 * * * cd /opt/kassel-agent && python src/cron_runner.py scrape

  # Check Gmail every 30 minutes
  */30 * * * * cd /opt/kassel-agent && python src/cron_runner.py emails

  # Full run (scrape + emails) twice daily
  0 8,18 * * * cd /opt/kassel-agent && python src/cron_runner.py full
"""

import sys
import asyncio
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / "data" / "cron.log")
    ]
)
log = logging.getLogger("cron_runner")


def run_scrape():
    from scraper_immoscout import run_scrape
    log.info("Starting scheduled ImmoScout24 scrape...")
    asyncio.run(run_scrape(dry_run=False))
    log.info("Scrape complete.")


def run_email_check():
    from gmail_checker import check_new_emails
    log.info("Starting scheduled Gmail check...")
    events = check_new_emails(hours_back=2)  # Last 2 hours for 30-min cron
    log.info(f"Email check complete: {len(events)} new events.")

    if events:
        for ev in events:
            log.info(f"  [{ev['type']}] {ev['subject']}")


def run_full():
    log.info("=" * 60)
    log.info("Starting full scheduled run (scrape + emails)")
    log.info("=" * 60)
    run_scrape()
    run_email_check()
    log.info("Full run complete.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python cron_runner.py [scrape|emails|full]")
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "scrape":
        run_scrape()
    elif mode == "emails":
        run_email_check()
    elif mode == "full":
        run_full()
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
