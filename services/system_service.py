"""
System service management functions.
Handles systemctl operations for bot restart/status control.
"""
import subprocess
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "mybot.service"

# Visudo instruction text
VISUDO_INSTRUCTIONS = """
⚠️ <b>Ruxsat xatoligi!</b>

Botni restart qilish uchun sudo ruxsati kerak.

<b>🔧 Sozlash:</b>

1️⃣ Serverga SSH orqali kiring
2️⃣ Quyidagi buyruqni bajaring:

<code>sudo visudo</code>

3️⃣ Faylning oxiriga quyidagilarni qo'shing:

<code>root ALL=(ALL) NOPASSWD: /bin/systemctl restart mybot.service
root ALL=(ALL) NOPASSWD: /bin/systemctl start mybot.service
root ALL=(ALL) NOPASSWD: /bin/systemctl stop mybot.service
root ALL=(ALL) NOPASSWD: /bin/systemctl status mybot.service
root ALL=(ALL) NOPASSWD: /bin/journalctl</code>

4️⃣ Saqlang va chiqing (Ctrl+X, Y, Enter)

<b>Eslatma:</b> Agar bot boshqa user ostida ishlayotgan bo'lsa, <code>root</code> o'rniga o'sha username ni yozing.
"""


def check_service_exists() -> bool:
    """Check if mybot.service exists."""
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=5
        )
        return SERVICE_NAME in result.stdout
    except Exception as e:
        logger.error(f"Error checking service existence: {e}")
        return False


def start_bot_service() -> Dict[str, any]:
    """Start the bot service."""
    try:
        # Check if service exists
        if not check_service_exists():
            return {
                "success": False,
                "message": f"❌ <b>{SERVICE_NAME} topilmadi!</b>\n\n"
                          f"Service yaratish kerak.\n\n"
                          f"<b>Service yaratish:</b>\n"
                          f"1. <code>sudo nano /etc/systemd/system/{SERVICE_NAME}</code>\n"
                          f"2. Service konfiguratsiyasini kiriting\n"
                          f"3. <code>sudo systemctl daemon-reload</code>\n"
                          f"4. <code>sudo systemctl enable {SERVICE_NAME}</code>"
            }
        
        # Try to start service
        result = subprocess.run(
            ["sudo", "systemctl", "start", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return {
                "success": True,
                "message": "✅ <b>Bot muvaffaqiyatli ishga tushirildi!</b>"
            }
        else:
            # Check for permission error
            if "permission denied" in result.stderr.lower() or result.returncode == 1:
                return {
                    "success": False,
                    "message": VISUDO_INSTRUCTIONS
                }
            return {
                "success": False,
                "message": f"❌ <b>Xatolik yuz berdi!</b>\n\n<code>{result.stderr}</code>"
            }
            
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": "❌ Buyruq juda uzoq davom etdi (timeout)."
        }
    except Exception as e:
        logger.error(f"Error starting service: {e}")
        return {
            "success": False,
            "message": f"❌ <b>Xatolik:</b> {str(e)}"
        }


def stop_bot_service() -> Dict[str, any]:
    """Stop the bot service."""
    try:
        # Check if service exists
        if not check_service_exists():
            return {
                "success": False,
                "message": f"❌ <b>{SERVICE_NAME} topilmadi!</b>"
            }
        
        # Try to stop service
        result = subprocess.run(
            ["sudo", "systemctl", "stop", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return {
                "success": True,
                "message": "⏹ <b>Bot to'xtatildi!</b>"
            }
        else:
            # Check for permission error
            if "permission denied" in result.stderr.lower() or result.returncode == 1:
                return {
                    "success": False,
                    "message": VISUDO_INSTRUCTIONS
                }
            return {
                "success": False,
                "message": f"❌ <b>Xatolik yuz berdi!</b>\n\n<code>{result.stderr}</code>"
            }
            
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": "❌ Buyruq juda uzoq davom etdi (timeout)."
        }
    except Exception as e:
        logger.error(f"Error stopping service: {e}")
        return {
            "success": False,
            "message": f"❌ <b>Xatolik:</b> {str(e)}"
        }


def restart_bot_service() -> Dict[str, any]:
    """Restart the bot service."""
    try:
        # Check if service exists
        if not check_service_exists():
            return {
                "success": False,
                "message": f"❌ <b>{SERVICE_NAME} topilmadi!</b>\n\n"
                          f"Service yaratish kerak."
            }
        
        # Try to restart service
        result = subprocess.run(
            ["sudo", "systemctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return {
                "success": True,
                "message": "✅ <b>Bot muvaffaqiyatli restart qilindi!</b>"
            }
        else:
            # Check for permission error
            if "permission denied" in result.stderr.lower() or result.returncode == 1:
                return {
                    "success": False,
                    "message": VISUDO_INSTRUCTIONS
                }
            return {
                "success": False,
                "message": f"❌ <b>Xatolik yuz berdi!</b>\n\n<code>{result.stderr}</code>"
            }
            
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": "❌ Buyruq juda uzoq davom etdi (timeout)."
        }
    except Exception as e:
        logger.error(f"Error restarting service: {e}")
        return {
            "success": False,
            "message": f"❌ <b>Xatolik:</b> {str(e)}"
        }


def get_bot_service_status() -> Dict[str, any]:
    """Get bot service status."""
    try:
        # Check if service exists
        if not check_service_exists():
            return {
                "success": False,
                "message": f"❌ <b>{SERVICE_NAME} topilmadi!</b>\n\n"
                          f"Service yaratish kerak."
            }
        
        # Get service status
        result = subprocess.run(
            ["systemctl", "status", SERVICE_NAME, "--no-pager"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Parse the status output
        status_lines = result.stdout.split('\n')
        
        # Extract key information
        status_info = {
            "active": "inactive",
            "running": False,
            "pid": "N/A",
            "uptime": "N/A",
            "memory": "N/A"
        }
        
        for line in status_lines:
            line = line.strip()
            if "Active:" in line:
                if "active (running)" in line.lower():
                    status_info["active"] = "active"
                    status_info["running"] = True
                    # Extract time info
                    if "since" in line:
                        parts = line.split("since")
                        if len(parts) > 1:
                            status_info["uptime"] = parts[1].strip()
                elif "inactive" in line.lower():
                    status_info["active"] = "inactive"
                elif "failed" in line.lower():
                    status_info["active"] = "failed"
            elif "Main PID:" in line:
                parts = line.split(":")
                if len(parts) > 1:
                    pid_info = parts[1].strip().split()[0]
                    status_info["pid"] = pid_info
            elif "Memory:" in line:
                parts = line.split(":")
                if len(parts) > 1:
                    status_info["memory"] = parts[1].strip()
        
        # Format the status message
        status_emoji = "✅" if status_info["running"] else "❌"
        status_text = "ACTIVE (ISHLAMOQDA)" if status_info["running"] else status_info["active"].upper()
        
        message = f"""
{status_emoji} <b>Bot holati</b>
────────────────

📌 <b>Status:</b> {status_text}
📌 <b>PID:</b> {status_info["pid"]}
📌 <b>Ishga tushgan vaqt:</b> {status_info["uptime"]}
📌 <b>Xotira:</b> {status_info["memory"]}

<b>Service:</b> {SERVICE_NAME}
        """.strip()
        
        return {
            "success": True,
            "message": message,
            "is_running": status_info["running"]
        }
            
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": "❌ Buyruq juda uzoq davom etdi (timeout)."
        }
    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        return {
            "success": False,
            "message": f"❌ <b>Xatolik:</b> {str(e)}"
        }


def get_bot_service_logs(lines: int = 30) -> Dict[str, any]:
    """Get bot service logs."""
    try:
        # Check if service exists
        if not check_service_exists():
            return {
                "success": False,
                "message": f"❌ <b>{SERVICE_NAME} topilmadi!</b>"
            }
        
        # Get service logs
        result = subprocess.run(
            ["journalctl", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            logs = result.stdout.strip()
            
            if not logs:
                return {
                    "success": True,
                    "message": "📜 <b>Loglar mavjud emas.</b>"
                }
            
            # Limit the log length for Telegram (max 4096 characters)
            if len(logs) > 3800:
                logs = logs[-3800:]
                logs = "... (kesib tashlangan)\n\n" + logs
            
            message = f"📜 <b>Oxirgi {lines} qator log:</b>\n\n<code>{logs}</code>"
            
            return {
                "success": True,
                "message": message
            }
        else:
            # Check for permission error
            if "permission denied" in result.stderr.lower():
                return {
                    "success": False,
                    "message": "❌ Log olishda xatolik: Ruxsat yo'q.\n\n"
                              "Journalctl uchun sudo ruxsati kerak bo'lishi mumkin."
                }
            return {
                "success": False,
                "message": f"❌ <b>Log olishda xatolik!</b>\n\n<code>{result.stderr}</code>"
            }
            
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": "❌ Buyruq juda uzoq davom etdi (timeout)."
        }
    except Exception as e:
        logger.error(f"Error getting service logs: {e}")
        return {
            "success": False,
            "message": f"❌ <b>Xatolik:</b> {str(e)}"
        }

