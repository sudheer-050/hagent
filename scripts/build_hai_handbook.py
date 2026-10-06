from pathlib import Path
from textwrap import wrap

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, PageBreak, Paragraph,
    Preformatted, Spacer, Table, TableStyle, KeepTogether,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'pdf' / 'HAI-Operations-and-Recovery-Handbook.pdf'
OUT.parent.mkdir(parents=True, exist_ok=True)

NAVY = colors.HexColor('#10233F')
BLUE = colors.HexColor('#2563EB')
CYAN = colors.HexColor('#0EA5A8')
PALE = colors.HexColor('#EAF2FF')
INK = colors.HexColor('#172033')
MUTED = colors.HexColor('#5B667A')
GREEN = colors.HexColor('#15803D')
RED = colors.HexColor('#B42318')
AMBER = colors.HexColor('#B45309')
LINE = colors.HexColor('#D8E0EC')

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CoverTitle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=27, leading=32, textColor=colors.white, alignment=TA_LEFT, spaceAfter=15))
styles.add(ParagraphStyle(name='CoverSub', parent=styles['BodyText'], fontName='Helvetica', fontSize=12, leading=18, textColor=colors.HexColor('#DCE9FF'), spaceAfter=12))
styles.add(ParagraphStyle(name='H1x', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=NAVY, spaceBefore=4, spaceAfter=10))
styles.add(ParagraphStyle(name='H2x', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=12, leading=15, textColor=BLUE, spaceBefore=8, spaceAfter=5))
styles.add(ParagraphStyle(name='Bodyx', parent=styles['BodyText'], fontName='Helvetica', fontSize=9.2, leading=13.5, textColor=INK, spaceAfter=6))
styles.add(ParagraphStyle(name='Smallx', parent=styles['BodyText'], fontName='Helvetica', fontSize=7.6, leading=10.5, textColor=MUTED))
styles.add(ParagraphStyle(name='Calloutx', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=NAVY, backColor=PALE, borderColor=BLUE, borderWidth=0.8, borderPadding=8, spaceBefore=5, spaceAfter=8))
styles.add(ParagraphStyle(name='Goodx', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=GREEN, backColor=colors.HexColor('#ECFDF3'), borderPadding=7, spaceAfter=7))
styles.add(ParagraphStyle(name='Warnx', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=AMBER, backColor=colors.HexColor('#FFF7E6'), borderPadding=7, spaceAfter=7))
styles.add(ParagraphStyle(name='Dangerx', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=9, leading=13, textColor=RED, backColor=colors.HexColor('#FFF0F0'), borderPadding=7, spaceAfter=7))
styles.add(ParagraphStyle(name='Stepx', parent=styles['BodyText'], fontName='Helvetica', fontSize=9, leading=13, leftIndent=13, firstLineIndent=-13, textColor=INK, spaceAfter=5))


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(0.65 * inch, 0.52 * inch, 7.85 * inch, 0.52 * inch)
    canvas.setFont('Helvetica', 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.65 * inch, 0.34 * inch, 'HAI Operations and Recovery Handbook')
    canvas.drawRightString(7.85 * inch, 0.34 * inch, f'Page {doc.page}')
    canvas.restoreState()


doc = BaseDocTemplate(str(OUT), pagesize=letter, leftMargin=0.65 * inch, rightMargin=0.65 * inch, topMargin=0.65 * inch, bottomMargin=0.7 * inch, title='HAI Operations and Recovery Handbook', author='Hagent / Holly')
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main')
doc.addPageTemplates(PageTemplate(id='handbook', frames=frame, onPage=footer))
story = []


def p(text, style='Bodyx'):
    story.append(Paragraph(text, styles[style]))


def h1(text):
    story.append(Paragraph(text, styles['H1x']))


def h2(text):
    story.append(Paragraph(text, styles['H2x']))


def step(number, text):
    story.append(Paragraph(f'<b>{number}.</b> {text}', styles['Stepx']))


def code(text):
    lines = []
    for raw in text.strip().splitlines():
        lines.extend(wrap(raw, width=92, subsequent_indent='  ', replace_whitespace=False, drop_whitespace=False) or [''])
    block = Preformatted('\n'.join(lines), ParagraphStyle(name='CodeLocal', fontName='Courier-Bold', fontSize=7.1, leading=9.5, textColor=NAVY, backColor=colors.HexColor('#EEF3F8'), borderColor=LINE, borderWidth=0.5, borderPadding=8, spaceBefore=4, spaceAfter=8))
    story.append(block)


def grid(rows, widths=None, header=True):
    data = [[Paragraph(str(cell), styles['Smallx']) for cell in row] for row in rows]
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign='LEFT')
    commands = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.45, LINE),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]
    if header:
        commands.extend([('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white)])
        for cell in data[0]:
            cell.style = ParagraphStyle(name='TableHeaderLocal', parent=styles['Smallx'], textColor=colors.white, fontName='Helvetica-Bold')
    table.setStyle(TableStyle(commands))
    story.append(table)
    story.append(Spacer(1, 8))


def new_page():
    story.append(PageBreak())


# Cover
cover = Table([[Paragraph('HAI', ParagraphStyle(name='Mark', fontName='Helvetica-Bold', fontSize=42, leading=44, textColor=colors.white)), Paragraph('OPERATIONS<br/>HANDBOOK', styles['CoverTitle'])]], colWidths=[1.35 * inch, 5.65 * inch])
cover.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), NAVY), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('BOX', (0, 0), (-1, -1), 0, NAVY), ('LEFTPADDING', (0, 0), (-1, -1), 18), ('RIGHTPADDING', (0, 0), (-1, -1), 18), ('TOPPADDING', (0, 0), (-1, -1), 28), ('BOTTOMPADDING', (0, 0), (-1, -1), 28)]))
story.append(Spacer(1, 0.75 * inch))
story.append(cover)
story.append(Spacer(1, 0.3 * inch))
p('Connection, launch, deployment, monitoring, backup, rollback, and recovery procedures for myhai.org.', 'CoverSub')
p('<b>Environment:</b> bigserver production + staging | <b>Assistant:</b> Holly in Hagent | <b>Updated:</b> 2026-09-21', 'CoverSub')
story.append(Spacer(1, 0.25 * inch))
p('SAFE OPERATING RULE', 'H2x')
p('Holly asks for a separate YES or NO before every state-changing step. Production permission is never inferred from staging permission. Every launch ends with a health check and a GO or NO-GO result.', 'Calloutx')
p('Keep this handbook available offline. It contains operational commands but intentionally contains no passwords, API keys, private SSH keys, database passwords, or Cloudflare credential contents.', 'Warnx')
new_page()

# 1. System map
h1('1. System map and source of truth')
p('The GitHub repository is the source of truth. Development happens in the laptop clone, staging is the proving ground, and production changes only after an explicit approval.')
grid([
    ['Item', 'Location', 'Purpose'],
    ['Repository', 'github.com/sudheer-050/HAI-messenger', 'Canonical code and Actions workflows'],
    ['Laptop clone', r'C:\Users\gsudh\hurricane-chat', 'Edit, test, commit, push'],
    ['Production', '/home/bigserver/hurricane-chat-prod', 'Live myhai.org stack'],
    ['Staging', '/home/bigserver/hurricane-chat-staging', 'Automatic staging deployment'],
    ['Old fallback', '/home/bigserver/hurricane-chat-prod-old', 'Retained recovery copy; do not delete'],
    ['Hagent / Holly', r'C:\Users\gsudh\Documents\projects\hagent', 'Guided launch and operations assistant'],
], widths=[1.15*inch, 2.75*inch, 3.1*inch])
h2('Traffic and persistent data')
grid([
    ['Service', 'Address / storage'],
    ['Production site', 'https://myhai.org'],
    ['Production API health', 'https://api.myhai.org/health'],
    ['Staging site', 'https://staging.myhai.org'],
    ['Staging API health', 'https://api-staging.myhai.org/health'],
    ['Uptime Kuma', 'bigserver 127.0.0.1:3001 through an SSH tunnel'],
    ['Database backups', '/home/bigserver/backups/hai-postgres'],
], widths=[2.2*inch, 4.8*inch])
p('Never use docker compose down -v. The -v flag deletes named volumes and can destroy PostgreSQL, Redis, or monitoring data.', 'Dangerx')

new_page()
# 2. Holly entry point
h1('2. Start every work session through Holly')
step(1, r'Double-click C:\Users\gsudh\Desktop\Open-Holly.cmd.')
step(2, 'Wait for the Holly chat window. The launcher starts Hagent if needed and prepares the private Uptime Kuma tunnel.')
step(3, 'Type: <b>start working</b>.')
step(4, 'Approve the read-only SSH connection to bigserver. Holly discovers and displays every server project.')
step(5, 'Choose a project number or name, then confirm its verified local and server folders.')
step(6, 'Holly locks every file and terminal action to that project. Answer each separate YES or NO question.')
step(7, 'Begin only after Holly reports GO. If she reports NO-GO, let her show the failed checks and ask before each repair.')
p('Holly has a permanent HAI Launch and Operations skill plus durable memories of the architecture, safety rules, and approval contract. Her terminal starts in the laptop HAI repository.', 'Goodx')
h2('Project lock and switching')
p('Mentioning another project does not switch the session. To change projects, say switch project to PROJECT_NAME. Holly stops current work, summarizes unfinished work, asks to end the current session, verifies the new folder, obtains a new project lock, clears old permissions, and starts the new checklist from the beginning.', 'Calloutx')
h2('Manual entry if the Desktop launcher is unavailable')
code('cd C:\\Users\\gsudh\\Documents\\projects\\hagent\npowershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\ensure_running.ps1\n# Then open Holly in Hagent and type: start working')
h2('The required launch questions')
grid([
    ['Gate', 'What Holly asks before acting'],
    ['Local readiness', 'Inspect repository, branch, changes, tools, and credentials?'],
    ['Server connection', 'Connect read-only to bigserver?'],
    ['Staging', 'Start or repair staging if it is unhealthy?'],
    ['Production', 'Start or repair production if unhealthy? Separate approval.'],
    ['Monitoring', 'Prepare the private Uptime Kuma tunnel?'],
    ['Final check', 'Run the complete read-only health check?'],
], widths=[1.35*inch, 5.65*inch])
h2('Current verified project mappings')
grid([
    ['Project', 'Local folder', 'bigserver folder'],
    ['HAI', r'C:\Users\gsudh\hurricane-chat', '/home/bigserver/hurricane-chat-prod and -staging'],
    ['Hagent', r'C:\Users\gsudh\Documents\projects\hagent', '/home/bigserver/apps/hagent'],
    ['AI Vault', r'C:\Users\gsudh\Documents\ai-vault', '/home/bigserver/ai-vault'],
    ['Auto Data Analyst', r'C:\Users\gsudh\Documents\projects\auto-data-analyst', '/home/bigserver/auto-data-analyst'],
], widths=[1.2*inch, 3.05*inch, 2.75*inch])
p('Holly refreshes the list from bigserver every time. Newly discovered folders are shown as Unmapped. Production, staging, and retained fallback folders for HAI are grouped as one project.', 'Calloutx')

new_page()
h1('3. Daily development launch sequence')
p('Holly announces the command, explains its effect, asks one question, and waits. Read-only inspection may be grouped only when you explicitly approve that inspection group.')
step(1, 'Confirm the laptop source folder and inspect Git state.')
code('cd C:\\Users\\gsudh\\hurricane-chat\ngit branch --show-current\ngit status --short\ngit remote -v')
step(2, 'Confirm bigserver is reachable and inspect the running services.')
code('ssh bigserver hostname\nssh bigserver uptime\nssh bigserver docker ps')
step(3, 'Check public endpoints. Expected: website HTTP 200 and API health HTTP 200.')
code('curl.exe -I https://myhai.org\ncurl.exe https://api.myhai.org/health\ncurl.exe -I https://staging.myhai.org\ncurl.exe https://api-staging.myhai.org/health')
step(4, 'Prepare the monitoring tunnel, then verify the dashboard if requested.')
code('ssh -N -L 3001:127.0.0.1:3001 bigserver\n# Browse to http://127.0.0.1:3001')
step(5, 'Holly reports GO or NO-GO. GO means you can describe the feature or fix you want.')
h2('Normal code flow')
code('git switch staging\ngit pull --ff-only\ngit switch -c feature/short-description\n# edit and run project tests\ngit add -A\ngit commit -m describe-the-change\ngit push -u origin feature/short-description')
p('Do not commit directly to master. Merge and deploy staging first, verify it, then request production approval.', 'Warnx')

new_page()
h1('4. Staging and production releases')
h2('Staging - automatic after merge')
step(1, 'Open a pull request from the feature branch into staging.')
step(2, 'Review checks and approve the merge.')
step(3, 'Watch the staging deployment and verify the staging site and API.')
code('gh pr create --base staging --head feature/short-description\ngh run list --limit 10\ngh run watch RUN_ID\ncurl.exe -I https://staging.myhai.org\ncurl.exe https://api-staging.myhai.org/health')
p('A successful workflow is not enough. The staging endpoints and service health must also pass.', 'Calloutx')
h2('Production - explicit approval every time')
step(1, 'Create a pull request from staging into master. Do not merge yet.')
step(2, 'Confirm staging health, tests, and the exact release changes.')
step(3, 'Holly asks: Approve production release? A previous YES never counts.')
step(4, 'After YES, merge. GitHub Actions pauses at the production environment gate; approve that gate.')
step(5, 'Watch deployment and run final health checks twice with a short observation interval.')
code('gh pr create --base master --head staging\ngh run list --limit 10\ngh run watch RUN_ID\ncurl.exe -I https://myhai.org\ncurl.exe https://api.myhai.org/health')

new_page()
h1('5. Connect to bigserver')
h2('Interactive shell')
code('ssh bigserver\nhostname\nuptime\npwd\nexit')
p('The SSH alias bigserver should already contain the correct host, user, and key selection. Never paste a private SSH key into chat, code, or this handbook.', 'Warnx')
h2('Run one command remotely')
code('ssh bigserver docker ps\nssh bigserver df -h\nssh bigserver free -h')
h2('Open private Uptime Kuma locally')
code('ssh -N -L 3001:127.0.0.1:3001 bigserver\n# Keep this terminal open. Browse to http://127.0.0.1:3001')
p('Kuma intentionally listens only on bigserver loopback. Do not expose port 3001 to the public internet.', 'Goodx')
h2('Important server folders')
grid([
    ['Path', 'Meaning'],
    ['/home/bigserver/hurricane-chat-prod', 'Current production checkout and Compose project'],
    ['/home/bigserver/hurricane-chat-staging', 'Current staging checkout and Compose project'],
    ['/home/bigserver/hurricane-chat-prod-old', 'Retained emergency fallback copy'],
    ['/home/bigserver/bin/backup-hai-postgres.sh', 'Nightly backup script'],
    ['/home/bigserver/backups/hai-postgres', 'Compressed database backups'],
], widths=[3.25*inch, 3.75*inch])

new_page()
h1('6. Start, stop, and restart services')
p('Ask separately before each command below. Prefer stop/start for planned interruption. Use down only when network or Compose recreation is necessary, and never add -v.', 'Calloutx')
h2('Production stack')
code('ssh bigserver\ncd /home/bigserver/hurricane-chat-prod\ndocker compose --profile tunnel -p hurricane-chat-prod ps\ndocker compose --profile tunnel -p hurricane-chat-prod stop\ndocker compose --profile tunnel -p hurricane-chat-prod start\ndocker compose --profile tunnel -p hurricane-chat-prod restart\ndocker compose --profile tunnel -p hurricane-chat-prod up -d --build')
h2('Staging stack')
code('ssh bigserver\ncd /home/bigserver/hurricane-chat-staging\ndocker compose -p hurricane-chat-staging ps\ndocker compose -p hurricane-chat-staging stop\ndocker compose -p hurricane-chat-staging start\ndocker compose -p hurricane-chat-staging restart\ndocker compose -p hurricane-chat-staging up -d --build')
h2('One service only')
code('docker restart CONTAINER_NAME\ndocker logs --tail 200 CONTAINER_NAME')
p('Forbidden without a recovery plan and explicit approval: docker compose down -v, docker volume rm, database deletion, or removing the retained old production directory.', 'Dangerx')

new_page()
h1('7. Health checks, status, and logs')
h2('Container and resource status')
code('ssh bigserver\ndocker ps --format table\ndocker stats --no-stream\ndf -h\nfree -h')
h2('Production diagnostics')
code('cd /home/bigserver/hurricane-chat-prod\ndocker compose --profile tunnel -p hurricane-chat-prod ps\ndocker compose --profile tunnel -p hurricane-chat-prod logs --tail 200 backend\ndocker compose --profile tunnel -p hurricane-chat-prod logs --tail 200 frontend\ndocker compose --profile tunnel -p hurricane-chat-prod logs --tail 200 db')
h2('Staging diagnostics')
code('cd /home/bigserver/hurricane-chat-staging\ndocker compose -p hurricane-chat-staging ps\ndocker compose -p hurricane-chat-staging logs --tail 200 backend\ndocker compose -p hurricane-chat-staging logs --tail 200 frontend')
h2('Dependencies and DNS')
code('getent hosts myhai.org\ngetent hosts api.myhai.org\ndocker exec CONTAINER_NAME pg_isready\ndocker exec CONTAINER_NAME redis-cli ping')
h2('Public checks from the laptop')
code('curl.exe -sS -o NUL -w HTTP:%{http_code} https://myhai.org\ncurl.exe -sS https://api.myhai.org/health\ncurl.exe -sS -o NUL -w HTTP:%{http_code} https://staging.myhai.org\ncurl.exe -sS https://api-staging.myhai.org/health')
p('Final GO requires expected HTTP responses, healthy required containers, no restart loop, working DNS, and green Uptime Kuma monitors.', 'Goodx')

new_page()
h1('8. GitHub Actions and the deployment runner')
p('Staging deploys automatically after an approved merge to staging. Production requires an approved merge to master and the protected production environment approval.')
h2('Workflow commands')
code('gh run list --repo sudheer-050/HAI-messenger --limit 15\ngh run view RUN_ID --log-failed\ngh run watch RUN_ID\ngh run rerun RUN_ID --failed')
h2('Runner service on bigserver')
code('ssh bigserver\nsudo systemctl status actions.runner.sudheer-050-HAI-messenger.bigserver.service\nsudo journalctl -u actions.runner.sudheer-050-HAI-messenger.bigserver.service -n 200\nsudo systemctl restart actions.runner.sudheer-050-HAI-messenger.bigserver.service')
p('Restarting the runner is state-changing. Holly must explain why and ask before doing it. Never bypass the production environment approval.', 'Warnx')
h2('Mandatory anti-skip deployment gate')
p('A request such as deploy now is not authorization to skip. Holly must complete, in order: repository preflight, clean or explained changes, tests, staging deployment, staging health, release review, fresh production YES, GitHub production gate, production deployment, and final health. Missing or failed gates mean NO-GO.', 'Dangerx')

new_page()
h1('9. Cloudflare Tunnel and routing')
p('Production includes the managed tunnel profile. Staging shares the approved routing design. Do not create a second competing tunnel.')
h2('Inspect and restart')
code('ssh bigserver\ncd /home/bigserver/hurricane-chat-prod\ndocker compose --profile tunnel -p hurricane-chat-prod ps\ndocker compose --profile tunnel -p hurricane-chat-prod logs --tail 200 cloudflared\ndocker compose --profile tunnel -p hurricane-chat-prod restart cloudflared')
h2('Configuration')
code('cd /home/bigserver/hurricane-chat-prod\nls -la cloudflared\nsed -n 1,200p cloudflared/config.yml')
p('The Cloudflare credential material is secret. Inspect presence and permissions, not its contents. Never include it in Git, chat, logs, screenshots, or this handbook.', 'Dangerx')
h2('Tunnel incident checks')
step(1, 'Confirm cloudflared is running and not restarting.')
step(2, 'Read recent tunnel logs for authentication, ingress, or DNS errors.')
step(3, 'Confirm the target frontend and backend containers are healthy on the expected Docker networks.')
step(4, 'Restart only cloudflared after approval. Recheck all four public endpoints.')

new_page()
h1('10. Uptime Kuma monitoring')
p('Kuma monitors production web, production API, staging web, and staging API. Email alerts are configured. The dashboard remains private.')
h2('Connect and inspect')
code('ssh -N -L 3001:127.0.0.1:3001 bigserver\n# Browse locally to http://127.0.0.1:3001\nssh bigserver docker ps --filter name=uptime-kuma')
h2('Restart monitoring')
code('ssh bigserver docker restart uptime-kuma\nssh bigserver docker logs --tail 200 uptime-kuma')
grid([
    ['Monitor', 'Expected'],
    ['Production website', 'HTTP 200 and green'],
    ['Production API', 'Health endpoint HTTP 200 and green'],
    ['Staging website', 'HTTP 200 and green'],
    ['Staging API', 'Health endpoint HTTP 200 and green'],
], widths=[2.6*inch, 4.4*inch])
p('A red monitor does not automatically prove the application is down. Compare public curl checks, container health, DNS, and tunnel logs before changing anything.', 'Calloutx')

new_page()
h1('11. PostgreSQL backups and restore')
h2('Inspect automatic backups')
code('ssh bigserver\ncrontab -l\nls -lh /home/bigserver/backups/hai-postgres\nfind /home/bigserver/backups/hai-postgres -type f -name *.sql.gz -mtime -2 -print')
h2('Run a manual backup')
code('ssh bigserver /home/bigserver/bin/backup-hai-postgres.sh\nssh bigserver ls -lh /home/bigserver/backups/hai-postgres')
h2('Validate an archive without restoring')
code('ssh bigserver gzip -t /home/bigserver/backups/hai-postgres/BACKUP_FILE.sql.gz')
p('A restore overwrites or adds database state. Stop, identify the exact database container and database name, preserve the current state with a fresh backup, and obtain explicit restore approval.', 'Dangerx')
h2('Controlled restore pattern')
step(1, 'Declare an incident and stop application writes if necessary.')
step(2, 'Make and validate a new backup of the current database.')
step(3, 'Select the exact archive and record its timestamp and size.')
step(4, 'Confirm the target Compose project, container, database, and owner.')
step(5, 'Ask for final restore YES. Only then execute the project-specific restore command.')
code('gunzip -c BACKUP_FILE.sql.gz | docker exec -i POSTGRES_CONTAINER psql -U DB_USER -d DB_NAME')
step(6, 'Restart application services only if needed, then run database, API, web, logs, and Kuma checks.')

new_page()
h1('12. Incident diagnosis')
grid([
    ['Symptom', 'First checks', 'Likely safe repair after YES'],
    ['502 or 504', 'Public curl, cloudflared logs, frontend/backend status', 'Restart failed app service; then tunnel only if required'],
    ['API HTTP 500', 'Backend logs, database and Redis health', 'Repair dependency or restart backend after cause is known'],
    ['Restart loop', 'docker ps, inspect, last 200 logs', 'Fix configuration or dependency; rebuild only after review'],
    ['Name resolution error', 'getent hosts, container DNS, Docker networks', 'Repair network attachment; do not repeatedly restart blindly'],
    ['Staging only fails', 'Staging workflow, Compose status, staging logs', 'Repair staging; production remains untouched'],
    ['Both sites fail', 'Host resources, tunnel, shared networks, DNS', 'Repair shared failing layer with separate approval'],
    ['Runner offline', 'systemctl status and journal', 'Restart runner service, then rerun failed job'],
    ['Kuma red only', 'Direct curls and Kuma logs', 'Repair monitor or restart Kuma; do not redeploy app'],
], widths=[1.35*inch, 2.75*inch, 2.9*inch])
h2('Universal triage order')
step(1, 'Observe and record the exact failure. Do not change anything yet.')
step(2, 'Check public response, container state, logs, dependencies, DNS, tunnel, and host capacity.')
step(3, 'State the most likely cause and the smallest repair.')
step(4, 'Ask one YES or NO question for that repair.')
step(5, 'Run the repair, verify it, and provide GO or NO-GO.')

new_page()
h1('13. Rollback a bad release')
p('Rollback is a production change. It requires the same complete preflight, an identified known-good commit, explicit approval, and a final health check.', 'Warnx')
step(1, 'Collect the failing release SHA, logs, health results, and the last known-good SHA.')
code('cd /home/bigserver/hurricane-chat-prod\ngit log --oneline -n 15\ngit status --short')
step(2, 'Confirm the chosen SHA exists on the trusted origin and explain what will be reverted.')
step(3, 'Make sure persistent data is compatible. A code rollback does not automatically reverse a database migration.')
step(4, 'Ask: Roll production back to GOOD_SHA? Wait for YES.')
code('git fetch origin\ngit checkout GOOD_SHA\ndocker compose --profile tunnel -p hurricane-chat-prod up -d --build')
step(5, 'Verify containers, logs, database connectivity, public endpoints, DNS, tunnel, and Kuma. Report GO or NO-GO.')
p('After emergency recovery, create the normal Git revert or corrective commit so GitHub again matches the deployed desired state.', 'Calloutx')

new_page()
h1('14. Safety and security rules')
grid([
    ['Always', 'Never'],
    ['Use the complete ordered checklist', 'Jump directly to deployment'],
    ['Ask separately before each state change', 'Treat one YES as blanket permission'],
    ['Verify staging before production', 'Infer production approval from staging approval'],
    ['Use secret stores and protected environment settings', 'Paste passwords, keys, tokens, or credential files'],
    ['Preserve named volumes and take backups', 'Use docker compose down -v'],
    ['Keep Kuma on 127.0.0.1 with SSH forwarding', 'Expose monitoring publicly'],
    ['Use the bigserver stack as deployment authority', 'Start the duplicate laptop Docker stack'],
    ['Keep the old production fallback until approved', 'Delete recovery assets during routine cleanup'],
], widths=[3.5*inch, 3.5*inch])
p('If a command differs from this handbook because the repository changed, stop and inspect the current Compose and workflow files. Do not guess.', 'Dangerx')

new_page()
h1('15. If you are lost: ask Holly or Codex')
p('You do not need to remember the current step. Both assistants should reconstruct state from the repository, GitHub, bigserver, public endpoints, and monitoring before recommending an action.')
h2('What to say')
code('start working\ncontinue HAI\nI am lost - find where we stopped and guide me\nrun the HAI preflight\nshow me the next safe step')
h2('Required assistant behavior')
step(1, 'Explain the currently observed state in simple language.')
step(2, 'Identify the last safely completed gate from evidence, not assumption.')
step(3, 'Restart the checklist at the earliest uncertain gate.')
step(4, 'Ask a separate YES or NO before every state-changing action.')
step(5, 'Never skip directly to staging or production deployment.')
step(6, 'Finish with the standard GO or NO-GO health report.')
p('This rule applies equally to Holly in Hagent and Codex in this workspace. If conversation memory is incomplete, the assistant must inspect current state and must not claim a step was completed without evidence.', 'Goodx')

new_page()
h1('16. Save and resume a project session')
p('Before finishing or switching, Holly must show PENDING WORK: completed work, branch, modified and untracked files, tests, blockers, and the exact next step. She must say clearly when the project is not ready to close.', 'Warnx')
step(1, 'Ask how code progress should be preserved: local files, an approved WIP commit, and an optionally approved push.')
step(2, 'Never automatically stash, discard, reset, clean, commit, or push.')
step(3, 'Ask: Save this project session handoff?')
step(4, 'After YES, save the project name, verified folder, branch, commits, completed work, pending files, tests, blockers, next step, and storage status.')
code('C:\\Users\\gsudh\\Documents\\projects\\hagent\\session-handoffs\\PROJECT_SLUG.md')
step(5, 'Report SESSION SAVED, show remaining pending items, and ask separately whether to end the current project session.')
step(6, 'When the project is selected later, ask whether to resume. Verify the handoff against current files and Git before continuing.')
p('A session handoff preserves context. Only a commit and push provide a GitHub backup, and both require their own approvals.', 'Calloutx')

new_page()
h1('17. Final GO / NO-GO report')
grid([
    ['Check', 'GO requirement'],
    ['Laptop source', 'Correct repository, branch understood, changes explained'],
    ['GitHub', 'Expected branch and workflow state; required checks passed'],
    ['bigserver', 'Reachable; disk and memory acceptable'],
    ['Production containers', 'Required services healthy, no restart loop'],
    ['Staging containers', 'Required services healthy, no restart loop'],
    ['Public production', 'Web and API return expected healthy responses'],
    ['Public staging', 'Web and API return expected healthy responses'],
    ['DNS and tunnel', 'Names resolve and cloudflared has no active fault'],
    ['Monitoring', 'Four Kuma monitors green; alerts configured'],
    ['Backups', 'Recent backup exists and archive validation is current'],
], widths=[2.1*inch, 4.9*inch])
h2('Report template')
code('RESULT: GO or NO-GO\nTIME: local time and server time\nPASSED: list every passed gate\nFAILED: list every failed or unknown gate\nCHANGES: every command that changed state\nNEXT: the next safe action, requiring a new YES if state-changing')
p('Only GO means work may proceed normally. NO-GO means stop at the failed gate, explain it, and request permission for the smallest repair.', 'Calloutx')

doc.build(story)
print(OUT)
