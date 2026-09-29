# Deploy — FSR Attendance Trigger

Run on the Email_automation VM (IST timezone).

## 1. Clone
```bash
cd /root
git clone https://github.com/dileepchowdary-git/fsr-attendance-trigger.git fsr_attendance_alerts
cd fsr_attendance_alerts
```

## 2. Config
```bash
cp .env.example .env
nano .env      # fill DB creds, MAIL_PROVIDER (+ its key), FROM_EMAIL, ALERT_CC
```

## 3. Virtual env + dependencies
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 4. Test (dry run — sends nothing, prints who would be mailed)
```bash
.venv/bin/python fsr_alerts.py --morning
.venv/bin/python fsr_alerts.py --night
```

## 5. Schedule (add the lines from crontab.txt)
```bash
crontab -e     # paste both lines from crontab.txt
crontab -l     # verify
```
Confirm the VM is on IST: `timedatectl` → Asia/Kolkata. If UTC, use 30 5 and 30 17 instead of 0 11 and 0 23.
