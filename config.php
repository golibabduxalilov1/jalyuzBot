<?php
/**
 * Bot Configuration
 */

// Load environment variables
require_once __DIR__ . '/vendor/autoload.php';
$dotenv = Dotenv\Dotenv::createImmutable(__DIR__);
$dotenv->load();

// Telegram Bot Token
define('BOT_TOKEN', ' 8403375028:AAGkL8WB_F5qsNoFN674Bk7gxfhJPXI67Ts');

// Google Sheets Configuration
define('GOOGLE_SHEET_ID', $_ENV['GOOGLE_SHEET_ID'] ?? getenv('GOOGLE_SHEET_ID'));
define('SERVICE_ACCOUNT_FILE', $_ENV['SERVICE_ACCOUNT_FILE'] ?? getenv('SERVICE_ACCOUNT_FILE') ?: 'service_account.json');
define('GOOGLE_WORKSHEET_NAME', $_ENV['GOOGLE_WORKSHEET_NAME'] ?? getenv('GOOGLE_WORKSHEET_NAME') ?: 'Sheet1');

// OpenAI Configuration
define('OPENAI_API_KEY', $_ENV['OPENAI_API_KEY'] ?? getenv('OPENAI_API_KEY'));
define('OPENAI_MODEL', $_ENV['OPENAI_MODEL'] ?? getenv('OPENAI_MODEL') ?: 'gpt-4o-mini');

// Default Image URL
define('DEFAULT_IMAGE_URL', $_ENV['DEFAULT_IMAGE_URL'] ?? getenv('DEFAULT_IMAGE_URL') ?: '');

// Admin Configuration
define('ADMINS', [8007366646]); // Asosiy adminlar
define('HELPER_ADMINS', [7947963208]); // Yordamchi adminlar

// Help phone number
define('HELP_PHONE', '97-310-31-11 raqamiga murojaat qiling.');

// Base directory
define('BASE_DIR', __DIR__);

