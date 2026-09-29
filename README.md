# FSR Attendance Trigger

Automated attendance alert mailer for Field Sales Reps (FSRs).

## Jobs
- **Morning (11:00 AM)** — emails FSRs not yet punched in: *punch in before 11:30 AM or Loss of Pay*.
- **Night (11:00 PM)** — emails FSRs who didn't punch out, and those with **< 3 completed field visits** (Loss of Pay).

Active FSRs only (`users.department_fk=2`, ACTIVE). Counts **Completed Field Visits** only. Skips Sundays.

## Run
```
python fsr_alerts.py --morning          # dry run (prints who would be mailed)
python fsr_alerts.py --morning --send   # actually send
python fsr_alerts.py --night   --send
```
Provider chosen by `MAIL_PROVIDER` in `.env` (mailchimp | brevo), with auto-fallback.

## Config
Copy `.env.example` to `.env` and fill in DB creds, mail keys, CC list.

## Cron (deploy)
```
0 11 * * * cd /root/fsr_attendance_alerts && .venv/bin/python fsr_alerts.py --morning --send >> alerts.log 2>&1
0 23 * * * cd /root/fsr_attendance_alerts && .venv/bin/python fsr_alerts.py --night   --send >> alerts.log 2>&1
```
