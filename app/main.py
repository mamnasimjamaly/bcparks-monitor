import asyncio
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Page, async_playwright

from telegram_alert import notify

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


async def select_park(page: Page, park_name: str):
    print(f"Selecting park: {park_name}")

    await page.click("#park-autocomplete-input")
    await page.fill("#park-autocomplete-input", park_name)
    await page.wait_for_timeout(2000)

    options = page.locator("mat-option")
    count = await options.count()

    print("Park options:", count)

    if count == 0:
        raise Exception(f"No park options found for {park_name}")

    await options.first.click()


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
    await page.wait_for_timeout(1000)

    options = page.locator("mat-option")
    count = await options.count()

    print("Equipment options found:", count)

    for i in range(count):
        print(i, await options.nth(i).inner_text())

    await page.get_by_text(equipment, exact=True).click()
    print(f"Equipment selected: {equipment}")


async def search(page: Page):
    print("Clicking search...")
    await page.click("#actionSearch")
    await page.wait_for_timeout(10000)


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


def screenshot_name(park_name: str, arrival: str, departure: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", park_name.lower()).strip("-")
    return f"result-{slug}-{arrival}-to-{departure}.png"


async def run_search(
    page: Page,
    park_name: str,
    arrival: str,
    departure: str,
    equipment: str,
) -> list[str]:
    print(f"\n=== {park_name} | {arrival} → {departure} | {equipment} ===")

    await page.goto(HOME_URL, wait_until="networkidle")
    print("Page loaded:", await page.title())

    await select_park(page, park_name)
    await select_dates(page, arrival, departure)
    await select_equipment(page, equipment)
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
        browser = await p.chromium.launch(
            headless=HEADLESS,
            slow_mo=0 if HEADLESS else 500,
        )
        page = await browser.new_page()

        for park_name in PARKS:
            for arrival, departure in date_ranges:
                try:
                    areas = await run_search(
                        page,
                        park_name,
                        arrival,
                        departure,
                        EQUIPMENT,
                    )
                except Exception as exc:
                    print(f"Search failed for {park_name} {arrival}→{departure}: {exc}")
                    results.append(
                        {
                            "park": park_name,
                            "arrival": arrival,
                            "departure": departure,
                            "areas": [],
                            "error": str(exc),
                        }
                    )
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
                f"{result['error']}"
            )


asyncio.run(main())
