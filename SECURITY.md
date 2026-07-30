# SECURITY.md · FinForge AI

## Permitted Actions
- Read/write files in /root/.hermes/, /var/www/artifacts/
- Execute Python scripts for bot and automation
- HTTP requests to DeepSeek API, Telegram API
- SQLite database operations (finforge.db)

## Denied Actions
- Read/write outside /root/.hermes/ without explicit approval
- Execute shell commands with sudo
- Access /root/.ssh/, /etc/ssl/, /etc/nginx/
- Send direct messages to users (only via bot API)
- Delete files without confirmation
- Access other users' data

## Override
- Explicit "сделай это" overrides deny list for single command
- Root access requires user confirmation

## Monitoring
- All denied access attempts logged
- Admin notified on suspicious patterns
