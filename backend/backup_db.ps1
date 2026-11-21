$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force -Path backups | Out-Null
docker compose exec -T processor python -c "import sqlite3; s=sqlite3.connect('/data/arm.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close(); s.close()"
docker compose cp processor:/data/backup.db "backups/arm_$stamp.db"
Write-Host "Backup saved: backups/arm_$stamp.db"