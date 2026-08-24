# BC Parks Monitor

Checks [camping.bcparks.ca](https://camping.bcparks.ca/) for campsite availability across multiple parks and date ranges. Sends a Telegram message only when something is open.

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

Chrome or Edge should also be installed. The monitor prefers those over bundled Chromium so the site is less likely to block it.

Copy `.env.example` to `.env` and fill in what you need:

```powershell
copy .env.example .env
```

`.env` is gitignored. Do not commit it.

## Configuration

All search settings live in `.env`. Missing values use defaults.

| Variable | Example | Default |
|---|---|---|
| `PARKS` | `Golden Ears,Alice Lake` | `Golden Ears` |
| `DATE_RANGES` | `2026-08-28:2026-08-30,2026-09-04:2026-09-06` | Next Friday–Sunday |
| `EQUIPMENT` | `2 Tents` | `2 Tents` |
| `HOME_URL` | `https://camping.bcparks.ca/` | that URL |
| `HEADLESS` | `true` | `false` for manual runs |
| `TELEGRAM_BOT_TOKEN` | bot token from [@BotFather](https://t.me/BotFather) | none (console only) |
| `TELEGRAM_CHAT_ID` | your chat id | none (console only) |

Date ranges are `arrival:departure`, comma-separated. Arrival must be today or later. Departure must be after arrival. Past ranges are skipped.

Every park is checked against every date range. Example: 3 parks × 3 weekends = 9 searches.

`.env` is read at the start of each run, so edits apply on the next run. You do not need to restart the scheduled task.

## Run once

From the `app` folder, with the venv active:

```powershell
.\venv\Scripts\Activate.ps1
cd app
python main.py
```

A browser window opens so you can watch the search. Telegram is optional.

## Run every 15 minutes

`run-monitor.bat` is what Task Scheduler should call. It:

- uses the project venv
- runs headless (`HEADLESS=1`)
- appends output to `logs\monitor.log`

If the task is not registered yet:

```powershell
$bat = "$PWD\run-monitor.bat"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $PWD
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 14) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "BC Parks Monitor" -Action $action -Trigger $trigger -Settings $settings -Force
```

Use `cmd.exe` with a quoted path. The project folder has a space in the user name, and Task Scheduler can fail without that.

```powershell
Start-ScheduledTask -TaskName "BC Parks Monitor"
Get-ScheduledTask -TaskName "BC Parks Monitor"
Get-Content .\logs\monitor.log -Tail 50
Unregister-ScheduledTask -TaskName "BC Parks Monitor" -Confirm:$false
```

The task runs while you are logged in. If a run is still going, the next one is skipped.

## How a run works

1. Opens the BC Parks search page once.
2. For each park, fills park and equipment, then searches each date range.
3. Extra dates for the same park reuse the form. A new park reloads the home form first.
4. If headless Chrome is blocked (page title `Azure WAF`), it retries with a visible window.
5. Telegram is sent only when available sites are found. Empty scans stay in the log.

## Logs

Scheduled output: `logs/monitor.log`

If the search form never appears, screenshots may be written to `logs/waf.png` or `logs/load-failed.png`. Manual (non-headless) runs also save a result screenshot per search in `logs/`.

## Troubleshooting

| Symptom | What to try |
|---|---|
| `The system cannot find the file specified` | The scheduled task is missing or the `.bat` path is wrong. Recreate it with `cmd.exe` as shown above. |
| `UnicodeEncodeError` / `charmap` | `run-monitor.bat` must set `PYTHONIOENCODING=utf-8`. |
| Page title `Azure WAF` | Site blocked the headless browser. The next attempt should open a visible Chrome/Edge window. |
| Stuck on the second park | Already handled by reloading the home form when the park changes. Check the latest log if it happens again. |
| No Telegram message | Normal when nothing is available. Confirm `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` if you expected a hit. |
