import asyncio
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Page, async_playwright

from telegram_alert import notify

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_HOME_URL = "https://camping.bcparks.ca/"
DEFAULT_PARKS = ["Golden Ears"]
DEFAULT_EQUIPMENT = "2 Tents"


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _parse_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def next_weekend() -> tuple[str, str]:
    """Upcoming Friday–Sunday. If today is Sat/Sun, use next week's weekend."""
    today = date.today()
    weekday = today.weekday()  # Monday=0 ... Sunday=6

    if weekday <= 4:
        days_until_friday = 4 - weekday
    else:
        days_until_friday = 11 - weekday

    friday = today + timedelta(days=days_until_friday)
    sunday = friday + timedelta(days=2)
    return friday.isoformat(), sunday.isoformat()


def _parse_date_ranges(value: str) -> list[tuple[str, str]]:
    ranges = []

    for item in _parse_list(value):
        if ":" not in item:
            raise ValueError(
                f"DATE_RANGES items must be start:end, got {item!r}"
            )

        arrival, departure = item.split(":", 1)
        ranges.append((arrival.strip(), departure.strip()))

    return ranges


def future_date_ranges(ranges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    today = date.today()
    valid = []

    for arrival, departure in ranges:
        try:
            start = datetime.strptime(arrival, "%Y-%m-%d").date()
            end = datetime.strptime(departure, "%Y-%m-%d").date()
        except ValueError:
            print(f"Skipping invalid date range {arrival} → {departure}")
            continue

        if end <= start:
            print(
                f"Skipping {arrival} → {departure}: "
                "departure must be after arrival"
            )
            continue

        if start < today:
            print(f"Skipping {arrival} → {departure}: arrival is in the past")
            continue

        valid.append((arrival, departure))

    return valid


HOME_URL = _env("HOME_URL") or DEFAULT_HOME_URL
PARKS = _parse_list(_env("PARKS")) or DEFAULT_PARKS
DATE_RANGES = _parse_date_ranges(_env("DATE_RANGES")) or [next_weekend()]
EQUIPMENT = _env("EQUIPMENT") or DEFAULT_EQUIPMENT
HEADLESS = _env("HEADLESS").lower() in ("1", "true", "yes")
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


async def launch_browser(playwright, headless: bool):
    launch_kwargs = {
        "headless": headless,
        "slow_mo": 0 if headless else 500,
        "args": ["--disable-blink-features=AutomationControlled"],
    }

    for channel in ("chrome", "msedge"):
        try:
            browser = await playwright.chromium.launch(
                channel=channel,
                **launch_kwargs,
            )
            print(f"Launched {channel} (headless={headless})")
            return browser
        except Exception as exc:
            print(f"Could not launch {channel}: {exc}")

    print(f"Launched bundled Chromium (headless={headless})")
    return await playwright.chromium.launch(**launch_kwargs)


async def new_page(browser) -> Page:
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1400, "height": 900},
        locale="en-CA",
        timezone_id="America/Vancouver",
    )
    page = await context.new_page()
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return page


def is_blocked_title(title: str) -> bool:
    lowered = title.lower()
    return any(
        marker in lowered
        for marker in ("waf", "access denied", "forbidden", "attention required")
    )


async def open_home(page: Page) -> None:
    await page.goto(HOME_URL, wait_until="domcontentloaded")
    title = await page.title()
    print("Page loaded:", title)

    if is_blocked_title(title):
        LOG_DIR.mkdir(exist_ok=True)
        await page.screenshot(path=str(LOG_DIR / "waf.png"), full_page=True)
        raise Exception(
            f"Blocked by site protection ({title}). "
            "The search form never loaded."
        )

    try:
        await page.locator("#park-autocomplete-input").wait_for(
            state="visible",
            timeout=15000,
        )
    except Exception:
        title = await page.title()
        LOG_DIR.mkdir(exist_ok=True)
        await page.screenshot(path=str(LOG_DIR / "load-failed.png"), full_page=True)
        raise Exception(f"Search form did not load (title: {title})")


async def ensure_search_form(page: Page) -> bool:
    """Bring the search form back without a full reload when possible.

    Returns True if the page was fully reloaded and form fields were reset.
    """
    park_input = page.locator("#park-autocomplete-input")

    try:
        if await park_input.is_visible():
            return False
    except Exception:
        pass

    print("Search form not visible; going back")
    await page.go_back()

    try:
        await park_input.wait_for(state="visible", timeout=10000)
        return False
    except Exception:
        print("Back did not restore the form; reloading home")
        await open_home(page)
        return True


async def select_park(page: Page, park_name: str):
    print(f"Selecting park: {park_name}")
    await page.keyboard.press("Escape")

    park_input = page.locator("#park-autocomplete-input")
    await park_input.click(timeout=8000)

    current = (await park_input.input_value()).strip()
    if current and current.lower() != park_name.lower():
        clear = page.locator(
            "mat-form-field:has(#park-autocomplete-input) button"
        )
        if await clear.count() > 0:
            try:
                await clear.first.click(timeout=2000)
            except Exception:
                await park_input.fill("")
        else:
            await park_input.fill("")

    await park_input.fill(park_name)

    option = page.locator("mat-option").first
    await option.wait_for(state="visible", timeout=8000)
    print("Park options:", await page.locator("mat-option").count())
    await option.click()


async def select_month(page: Page, target_date: str):
    target = datetime.strptime(target_date, "%Y-%m-%d")

    while True:
        calendar_header = await page.locator("#monthDropdownPicker").inner_text()
        calendar_header = calendar_header.strip()

        print("Calendar showing:", calendar_header)

        current = datetime.strptime(calendar_header, "%b %Y")

        if current.year == target.year and current.month == target.month:
            print("Reached target month")
            break

        if current < target:
            print("Click next month")
            await page.locator("#nextYearButton").click()
        else:
            print("Click previous month")
            await page.locator(".prev-button:not([disabled])").click()

        await page.wait_for_timeout(500)


async def select_day(page: Page, day: int):
    print("Selecting day:", day)

    buttons = page.locator("button")
    count = await buttons.count()

    for i in range(count):
        button = buttons.nth(i)

        try:
            text = (await button.inner_text()).strip()

            if text != str(day):
                continue

            classes = await button.get_attribute("class")

            if classes and "calendar" in classes:
                await button.click()
                print("Clicked day:", day)
                return
        except Exception:
            pass

    raise Exception(f"Could not find calendar day {day}")


async def select_dates(page: Page, arrival_date: str, departure_date: str):
    arrival = datetime.strptime(arrival_date, "%Y-%m-%d")
    departure = datetime.strptime(departure_date, "%Y-%m-%d")

    print("Opening date picker")
    await page.click("#arrival-date-field")
    await page.wait_for_timeout(1000)

    await select_month(page, arrival_date)
    await select_day(page, arrival.day)
    print("Arrival selected")

    # Do not click departure field.
    # BC Parks automatically moves to departure selection.
    await page.wait_for_timeout(1000)

    print("Selecting departure")
    await select_month(page, departure_date)
    await select_day(page, departure.day)
    print("Departure selected")


async def select_equipment(page: Page, equipment: str):
    print(f"Selecting equipment: {equipment}")

    await page.click("#equipment-field")
    option = page.get_by_role("option", name=equipment, exact=True)
    await option.wait_for(state="visible", timeout=8000)
    await option.click()
    print(f"Equipment selected: {equipment}")


async def search(page: Page):
    print("Clicking search...")
    await page.click("#actionSearch")
    try:
        await page.locator(".leaflet-container").wait_for(
            state="visible",
            timeout=15000,
        )
    except Exception:
        await page.wait_for_timeout(3000)


async def find_available_areas(page: Page) -> list[str]:
    available = page.locator(
        '.leaflet-marker-icon svg[data-availability="icon-available"]'
    )
    count = await available.count()

    if count == 0:
        print("No available areas.")
        return []

    print("Available areas:")
    areas = []

    for i in range(count):
        area = await available.nth(i).get_attribute("id")

        if area:
            areas.append(area)
            print(f"🟢 {area}")

    return areas


def screenshot_name(park_name: str, arrival: str, departure: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", park_name.lower()).strip("-")
    LOG_DIR.mkdir(exist_ok=True)
    return LOG_DIR / f"result-{slug}-{arrival}-to-{departure}.png"


def short_error(message: str) -> str:
    return message.splitlines()[0]


async def run_search(
    page: Page,
    park_name: str,
    arrival: str,
    departure: str,
    equipment: str,
    *,
    change_park: bool,
    fresh_home: bool,
) -> list[str]:
    print(f"\n=== {park_name} | {arrival} → {departure} | {equipment} ===")

    if change_park:
        if not fresh_home:
            print("Changing park; reloading search form")
            await open_home(page)
        await select_park(page, park_name)
        await select_equipment(page, equipment)
    else:
        reloaded = await ensure_search_form(page)
        if reloaded:
            await select_park(page, park_name)
            await select_equipment(page, equipment)

    await select_dates(page, arrival, departure)
    await search(page)

    areas = await find_available_areas(page)

    if not HEADLESS:
        await page.screenshot(
            path=screenshot_name(park_name, arrival, departure),
            full_page=True,
        )

    return areas


async def main():
    print(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===")

    date_ranges = future_date_ranges(DATE_RANGES)

    print("Search settings:")
    print(f"  URL: {HOME_URL}")
    print(f"  Parks: {', '.join(PARKS)}")
    print(f"  Dates: {', '.join(f'{start} → {end}' for start, end in date_ranges)}")
    print(f"  Equipment: {EQUIPMENT}")
    print(f"  Headless: {HEADLESS}")

    if not date_ranges:
        print("No future date ranges to search. Update DATE_RANGES in .env.")
        return

    results = []

    async with async_playwright() as p:
        headless = HEADLESS
        browser = await launch_browser(p, headless)
        page = await new_page(browser)

        try:
            await open_home(page)
        except Exception as exc:
            if headless and "Blocked by site protection" in str(exc):
                print("Headless browser was blocked; retrying with a visible window")
                await browser.close()
                headless = False
                browser = await launch_browser(p, headless)
                page = await new_page(browser)
                try:
                    await open_home(page)
                except Exception as retry_exc:
                    await browser.close()
                    print(f"Could not load BC Parks: {retry_exc}")
                    return
            else:
                await browser.close()
                print(f"Could not load BC Parks: {exc}")
                return

        stop = False
        fresh_home = True
        for park_name in PARKS:
            if stop:
                break
            change_park = True
            for arrival, departure in date_ranges:
                try:
                    areas = await run_search(
                        page,
                        park_name,
                        arrival,
                        departure,
                        EQUIPMENT,
                        change_park=change_park,
                        fresh_home=fresh_home,
                    )
                    change_park = False
                    fresh_home = False
                except Exception as exc:
                    fresh_home = False
                    print(f"Search failed for {park_name} {arrival}→{departure}: {short_error(str(exc))}")
                    results.append(
                        {
                            "park": park_name,
                            "arrival": arrival,
                            "departure": departure,
                            "areas": [],
                            "error": str(exc),
                        }
                    )
                    if "Blocked by site protection" in str(exc):
                        print("Stopping remaining searches.")
                        stop = True
                        break
                    continue

                results.append(
                    {
                        "park": park_name,
                        "arrival": arrival,
                        "departure": departure,
                        "areas": areas,
                        "error": None,
                    }
                )

                if areas:
                    message = (
                        "🏕️ BC Parks Availability Found!\n\n"
                        f"Park: {park_name}\n"
                        f"Dates: {arrival} → {departure}\n"
                        f"Equipment: {EQUIPMENT}\n\n"
                        + "\n".join(f"🟢 {area}" for area in areas)
                    )
                    await notify(message)

        await browser.close()

    hits = [result for result in results if result["areas"]]
    errors = [result for result in results if result["error"]]

    print(
        f"\nScan complete. Checked {len(results)} searches "
        f"({len(PARKS)} parks × {len(date_ranges)} date ranges)."
    )

    if hits:
        print(f"Availability found in {len(hits)}:")
        for result in hits:
            print(
                f"- {result['park']} ({result['arrival']} → {result['departure']}): "
                f"{len(result['areas'])} areas"
            )
    else:
        print("No availability found.")

    if errors:
        print(f"{len(errors)} searches failed:")
        for result in errors:
            print(
                f"- {result['park']} ({result['arrival']} → {result['departure']}): "
                f"{short_error(result['error'])}"
            )


asyncio.run(main())
