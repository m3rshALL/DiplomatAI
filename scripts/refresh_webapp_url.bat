@echo off
setlocal enableextensions enabledelayedexpansion

REM Обновляет WEBAPP_URL в .env, подтягивая актуальный trycloudflare URL из логов cloudflared (docker-compose service: tunnel).
REM Зачем: quick tunnel URL меняется после перезапуска, старый домен даёт ERR_NAME_NOT_RESOLVED в Telegram Mini App.

if not exist .env (
  echo ❌ .env file not found.
  echo Create it first: copy env.example .env
  exit /b 1
)

echo.
echo 🔄 Starting/updating web + tunnel...
docker-compose up -d web tunnel >nul 2>&1

echo.
echo ⏳ Waiting a bit for tunnel to print URL...
timeout /t 3 /nobreak >nul

set "URL="
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command ^
  "$m=(docker-compose logs tunnel ^| Select-String -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches).Matches.Value; ^
   if(-not $m -or $m.Count -eq 0){ exit 1 }; ^
   $m[-1]"`) do set "URL=%%U"

if "%URL%"=="" (
  echo ❌ Could not detect trycloudflare URL.
  echo Check tunnel logs: docker-compose logs -f tunnel
  exit /b 1
)

echo ✅ Found: %URL%

echo.
echo ✍ Updating WEBAPP_URL in .env...
powershell -NoProfile -Command ^
  "$path='.env'; $url='%URL%'; $c=Get-Content -Path $path -Raw; ^
   if($c -match '(?m)^WEBAPP_URL='){ ^
     $c=[regex]::Replace($c,'(?m)^WEBAPP_URL=.*$','WEBAPP_URL='+$url); ^
   } else { ^
     if($c -and -not $c.EndsWith(\"`n\")){ $c += \"`r`n\" }; ^
     $c += 'WEBAPP_URL='+$url+\"`r`n\"; ^
   }; ^
   Set-Content -Path $path -Value $c -Encoding utf8"

echo.
echo 🔁 Restarting tunnel + bot (to pick up new WEBAPP_URL)...
docker-compose restart tunnel bot >nul 2>&1

echo.
echo 🔎 Checking /health via the new URL...
powershell -NoProfile -Command ^
  "$u='%URL%/health'; ^
   try { ^
     $r=Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 15; ^
     if(($r.Content -as [string]).Trim() -ne 'ok'){ throw 'unexpected health response' }; ^
     Write-Host '✅ health ok'; ^
   } catch { ^
     Write-Host '⚠️  tunnel url is not reachable yet (try again in 10-30s):' $u; ^
   }"

echo.
echo ✅ Done. Use /app in Telegram bot again.

