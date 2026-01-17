# reset_db.ps1
$ErrorActionPreference = "Stop"

$dbPath = ".\app.db"

if (Test-Path $dbPath) {
  Remove-Item $dbPath -Force
  Write-Host "SQLite deleted: $dbPath"
} else {
  Write-Host "No SQLite file found at: $dbPath"
}

# Recrée les tables
python -c "from app.db.database import Base, engine; import app.db.models; Base.metadata.create_all(bind=engine); print('DB tables created')"
