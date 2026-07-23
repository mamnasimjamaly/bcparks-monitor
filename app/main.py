import asyncio
from playwright.async_api import async_playwright
from datetime import datetime


PARK_NAME = "Golden Ears"

ARRIVAL_DATE = "2026-09-15"
DEPARTURE_DATE = "2026-09-17"


async def select_park(page):

    print("Selecting park...")

    await page.click(
        "#park-autocomplete-input"
    )

    await page.fill(
        "#park-autocomplete-input",
        PARK_NAME
    )

    # wait for Angular autocomplete dropdown
    await page.wait_for_timeout(2000)

    options = page.locator(
        "mat-option"
    )

    count = await options.count()

    print("Park options:", count)

    if count > 0:
        await options.first.click()
    else:
        raise Exception(
            "No park options found"
        )


async def select_month(page: Page, target_date: str):

    target = datetime.strptime(
        target_date,
        "%Y-%m-%d"
    )

    while True:

        # BC Parks current month button
        calendar_header = await page.locator(
            "#monthDropdownPicker"
        ).inner_text()

        calendar_header = calendar_header.strip()

        print("Calendar showing:", calendar_header)

        current = datetime.strptime(
            calendar_header,
            "%b %Y"
        )


        # Already on target month
        if (
            current.year == target.year
            and current.month == target.month
        ):
            print("Reached target month")
            break


        # Move forward
        if current < target:

            print("Click next month")

            await page.locator(
                "#nextYearButton"
            ).click()


        # Move backward
        else:

            print("Click previous month")

            await page.locator(
                ".prev-button:not([disabled])"
            ).click()


        await page.wait_for_timeout(500)



async def select_day(page: Page, day: int):

    print("Selecting day:", day)

    # BC Parks date buttons
    buttons = page.locator(
        "button"
    )

    count = await buttons.count()

    for i in range(count):

        button = buttons.nth(i)

        try:
            text = (await button.inner_text()).strip()

            if text == str(day):

                # Make sure it is a calendar day button
                classes = await button.get_attribute(
                    "class"
                )

                if classes and "calendar" in classes:

                    await button.click()

                    print(
                        "Clicked day:",
                        day
                    )

                    return

        except:
            pass


    raise Exception(
        f"Could not find calendar day {day}"
    )



async def select_dates(page: Page):

    arrival_date = datetime.strptime(
        ARRIVAL_DATE,
        "%Y-%m-%d"
    )

    departure_date = datetime.strptime(
        DEPARTURE_DATE,
        "%Y-%m-%d"
    )


    # Open calendar
    print("Opening date picker")

    await page.click(
        "#arrival-date-field"
    )

    await page.wait_for_timeout(1000)


    # Move to arrival month
    await select_month(
        page,
        ARRIVAL_DATE
    )


    # Select arrival
    await select_day(
        page,
        arrival_date.day
    )

    print(
        "Arrival selected"
    )


    # IMPORTANT:
    # Do not click departure field.
    # BC Parks automatically moves to departure selection.

    await page.wait_for_timeout(1000)


    # Check what month calendar is showing
    print(
        "Selecting departure"
    )


    await select_month(
        page,
        DEPARTURE_DATE
    )


    await select_day(
        page,
        departure_date.day
    )


    print(
        "Departure selected"
    )


async def select_equipment(page):

    print("Selecting equipment...")

    await page.click(
        "#equipment-field"
    )

    await page.wait_for_timeout(1000)

    # Print available options for debugging
    options = page.locator("mat-option")

    count = await options.count()

    print("Equipment options found:", count)

    for i in range(count):
        print(
            i,
            await options.nth(i).inner_text()
        )

    # Select "2 Tents"
    await page.get_by_text(
        "2 Tents",
        exact=True
    ).click()

    print("Equipment selected: 2 Tents")


async def search(page):

    print(
        "Clicking search..."
    )

    await page.click(
        "#actionSearch"
    )

    await page.wait_for_timeout(
        10000
    )


async def main():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=False,
            slow_mo=500
        )

        page = await browser.new_page()

        # log requests for debugging
        # page.on(
        #     "request",
        #     lambda req:
        #     print(
        #         "REQ:",
        #         req.method,
        #         req.url
        #     )
        # )

        await page.goto(
            "https://camping.bcparks.ca/",
            wait_until="networkidle"
        )

        print(
            "Page loaded:",
            await page.title()
        )


        await select_park(page)

        await select_dates(page)

        await select_equipment(page)

        await search(page)


        print(
            "\nRESULT PAGE TEXT\n"
        )

        text = await page.locator(
            "body"
        ).inner_text()

        print(
            text[:5000]
        )


        await page.screenshot(
            path="result.png",
            full_page=True
        )

        await browser.close()


asyncio.run(main())