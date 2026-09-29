#!/usr/bin/env python3
"""
FSR attendance alert mailer.

Two modes, run by cron:
  --morning : (run 11:00 AM)  email FSRs who have NOT punched in yet
              -> "punch in before 11:30 AM or it is marked Loss of Pay"
  --night   : (run 11:00 PM)  email FSRs who
                (a) punched in but did NOT punch out  -> "please punch out"
                (b) completed < 3 field visits today  -> "<3 meetings is Loss of Pay"

Safe by default: pass --send to actually send; without it, it's a DRY RUN
(prints exactly who would be mailed and why, sends nothing).

Provider is chosen by MAIL_PROVIDER in .env (mailchimp | brevo), same pattern
as the rest of the stack, with automatic fallback to the other provider.
"""
from __future__ import annotations
import os, sys, argparse, datetime as dt
import psycopg2, psycopg2.extras, requests
from pathlib import Path
from dotenv import dotenv_values
import mailchimp_transactional as MailchimpTransactional
from mailchimp_transactional.api_client import ApiClientError

HERE = Path(__file__).resolve().parent
env = dotenv_values(HERE / ".env")

MEETING_TARGET   = int(env.get("MEETING_TARGET", "3"))
MORNING_DEADLINE = env.get("MORNING_DEADLINE", "11:30 AM")
FROM_EMAIL       = (env.get("FROM_EMAIL") or "insights@5cnetwork.com").strip().strip('"')
CC_EMAILS        = [e.strip() for e in (env.get("ALERT_CC","") or "").split(",") if e.strip()]
MAIL_PROVIDER    = (env.get("MAIL_PROVIDER") or "brevo").strip().lower()

def db():
    return psycopg2.connect(dbname=env.get("POSTGRES_DB","yaake"),
        user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"],
        host=env["POSTGRES_HOST"], port=int(env.get("POSTGRES_PORT","5432")))

# ---------------- data ----------------
def active_fsrs(cur):
    cur.execute("""SELECT id, name, email FROM users
                   WHERE department_fk=2 AND status='ACTIVE' AND email IS NOT NULL""")
    return [dict(id=r[0], name=r[1], email=r[2].strip().lower())
            for r in cur.fetchall() if r[2]]

def punches_today(cur):
    cur.execute("""SELECT lower(email) email, in_time, out_time FROM punching_info
                   WHERE date::date = CURRENT_DATE""")
    return {r[0]: dict(in_time=r[1], out_time=r[2]) for r in cur.fetchall()}

def completed_visits_today(cur):
    """email -> list of (client_name, time_str) for today's Completed field visits."""
    cur.execute("""SELECT lower(m.by_user) email, l.lead_name,
                          to_char(m.event_at AT TIME ZONE 'Asia/Kolkata','HH12:MI AM') t
                   FROM meetings m LEFT JOIN lead l ON l.id=m.client_fk
                   WHERE m.status='Completed' AND m.meeting_type='Field Visit'
                     AND m.event_at::date = CURRENT_DATE
                   ORDER BY m.event_at""")
    d={}
    for email, name, t in cur.fetchall():
        d.setdefault(email, []).append((name or "(unknown client)", t or ""))
    return d

# ---------------- mail ----------------
def _send_mailchimp(to, subject, html):
    if not env.get("MAILCHIMP_API_KEY"): return 500
    try:
        r = MailchimpTransactional.Client(env["MAILCHIMP_API_KEY"]).messages.send({"message":{
            "from_email":FROM_EMAIL,"from_name":"5C Attendance","subject":subject,"html":html,
            "to":[{"email":to,"type":"to"}]+[{"email":c,"type":"cc"} for c in CC_EMAILS]}})
        ok = r and r[0].get("status") in ("sent","queued") and not r[0].get("reject_reason") and not r[0].get("queued_reason")
        return 202 if ok else 500
    except ApiClientError: return 500

def _send_brevo(to, subject, html):
    if not env.get("BREVO_API_KEY"): return 500
    p={"sender":{"name":"5C Attendance","email":FROM_EMAIL},"to":[{"email":to}],"subject":subject,"htmlContent":html}
    if CC_EMAILS: p["cc"]=[{"email":c} for c in CC_EMAILS]
    try:
        r=requests.post("https://api.brevo.com/v3/smtp/email",
            headers={"api-key":env["BREVO_API_KEY"],"content-type":"application/json","accept":"application/json"},
            json=p,timeout=30)
        return 202 if r.status_code==201 else 500
    except requests.RequestException: return 500

_PROV={"mailchimp":_send_mailchimp,"brevo":_send_brevo}
def send_mail(to, subject, html):
    primary = MAIL_PROVIDER if MAIL_PROVIDER in _PROV else "brevo"
    if _PROV[primary](to,subject,html)==202: return True
    other=next(p for p in _PROV if p!=primary)
    return _PROV[other](to,subject,html)==202

def _visits_table(visits):
    """visits = list of (client_name, time). Returns an HTML block."""
    if visits is None:
        return ""
    if not visits:
        return ("<p style='margin:18px 0 4px;font-weight:600;color:#1f3355'>Completed field visits today: 0</p>"
                "<p style='margin:0;color:#8a8f98;font-style:italic'>No completed field visits are recorded for you today.</p>")
    rows="".join(
        f"<tr>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #eef0f3;color:#555;width:34px'>{i+1}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #eef0f3;color:#222'>{c}</td>"
        f"<td style='padding:8px 12px;border-bottom:1px solid #eef0f3;color:#555;white-space:nowrap'>{t}</td>"
        f"</tr>" for i,(c,t) in enumerate(visits))
    return (f"<p style='margin:18px 0 8px;font-weight:600;color:#1f3355'>Completed field visits today: {len(visits)}</p>"
            f"<table style='border-collapse:collapse;width:100%;font-size:13px;border:1px solid #eef0f3;border-radius:6px'>"
            f"<tr style='background:#f4f6f9'>"
            f"<th style='text-align:left;padding:8px 12px;color:#1f3355;font-size:12px'>#</th>"
            f"<th style='text-align:left;padding:8px 12px;color:#1f3355;font-size:12px'>Client</th>"
            f"<th style='text-align:left;padding:8px 12px;color:#1f3355;font-size:12px'>Time</th></tr>"
            f"{rows}</table>")

def render_email(name, headline, body_html, visits=None, accent="#c0392b"):
    """Styled attendance-alert email. accent = headline/bar colour."""
    today = dt.date.today().strftime("%d %b %Y")
    return f"""\
<div style="margin:0;padding:24px 0;background:#eef1f5;font-family:'Segoe UI',Arial,sans-serif">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0"
           style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;
                  box-shadow:0 1px 4px rgba(0,0,0,.08)">
      <tr><td style="background:#1f3355;padding:18px 28px">
        <span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:.3px">5C Network</span>
        <span style="color:#9fb3d1;font-size:13px;float:right;padding-top:4px">Attendance Alert &middot; {today}</span>
      </td></tr>
      <tr><td style="height:4px;background:{accent}"></td></tr>
      <tr><td style="padding:26px 28px 8px">
        <p style="margin:0 0 14px;font-size:14px;color:#333">Hi {name},</p>
        <p style="margin:0 0 14px;font-size:16px;font-weight:700;color:{accent}">{headline}</p>
        <div style="font-size:14px;color:#333;line-height:1.55">{body_html}</div>
        {_visits_table(visits)}
      </td></tr>
      <tr><td style="padding:20px 28px 26px">
        <div style="border-top:1px solid #eef0f3;padding-top:14px;color:#9298a1;font-size:12px;line-height:1.5">
          This is an automated attendance alert from the 5C Sales system. If you believe this is an error
          (e.g. you were on approved leave), please contact your reporting manager.
        </div>
      </td></tr>
    </table>
  </td></tr></table>
</div>"""

# ---------------- runs ----------------
def run_morning(send):
    with db() as c, c.cursor() as cur:
        fsrs=active_fsrs(cur); punch=punches_today(cur)
    flagged=[f for f in fsrs if not (punch.get(f["email"]) and punch[f["email"]]["in_time"])]
    print(f"[MORNING] active FSRs={len(fsrs)} | NOT punched in={len(flagged)}")
    for f in flagged:
        subj="Action needed: Punch in before %s" % MORNING_DEADLINE
        headline=f"Please punch in before {MORNING_DEADLINE}"
        body=("<p>Our records show you have <b>not punched in</b> yet today.</p>"
              f"<p>Please punch in before <b>{MORNING_DEADLINE}</b>. If not, today will be "
              "marked as <b style='color:#c0392b'>Loss of Pay</b>.</p>")
        _act(send, f, subj, render_email(f["name"], headline, body))

def run_night(send):
    with db() as c, c.cursor() as cur:
        fsrs=active_fsrs(cur); punch=punches_today(cur); visits=completed_visits_today(cur)
    flagged=[]
    for f in fsrs:
        p=punch.get(f["email"]); vlist=visits.get(f["email"], []); v=len(vlist)
        issues=[]; show_visits=False
        if p and p["in_time"] and not p["out_time"]:
            issues.append("<p>You punched in today but <b>did not punch out</b>. "
                          "Please <b>punch out</b> now to close your attendance for the day.</p>")
        if v < MEETING_TARGET:
            issues.append(f"<p>You completed <b>{v} of {MEETING_TARGET}</b> required field visits today. "
                          f"Completing fewer than {MEETING_TARGET} meetings a day will be marked as "
                          "<b style='color:#c0392b'>Loss of Pay</b>.</p>")
            show_visits=True
        if issues:
            flagged.append(f)
            html=render_email(f["name"], "Action needed for today's attendance",
                              "".join(issues), visits=(vlist if show_visits else None))
            _act(send, f, "Action needed: Attendance / meetings for today", html)
    print(f"[NIGHT] active FSRs={len(fsrs)} | flagged={len(flagged)} "
          f"(no punch-out or <{MEETING_TARGET} visits)")

def _act(send, f, subj, html):
    if send:
        ok=send_mail(f["email"], subj, html)
        print(f"  {'SENT ' if ok else 'FAIL '} {f['name']:<28} {f['email']}")
    else:
        print(f"  WOULD-MAIL  {f['name']:<28} {f['email']}")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--morning",action="store_true")
    ap.add_argument("--night",action="store_true")
    ap.add_argument("--send",action="store_true",help="actually send (default is dry-run)")
    ap.add_argument("--force",action="store_true",help="run even on Sunday")
    a=ap.parse_args()
    today=dt.date.today()
    start=(env.get("START_DATE") or "").strip()
    if start and not a.force and str(today) < start:
        print(f"Before START_DATE ({start}) — skipping (today {today})."); return
    if today.weekday()==6 and not a.force:      # Sunday
        print("Sunday — skipping (use --force to override)."); return
    if not (a.morning or a.night):
        print("Pass --morning or --night"); sys.exit(1)
    print(f"=== {'SEND' if a.send else 'DRY RUN'} | {today} {today.strftime('%A')} | provider={MAIL_PROVIDER} ===")
    if a.morning: run_morning(a.send)
    if a.night:   run_night(a.send)

if __name__=="__main__":
    main()
