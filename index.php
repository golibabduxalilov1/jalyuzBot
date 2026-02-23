<?php
/**
 * Main Bot Entry Point
 */

require_once __DIR__ . '/vendor/autoload.php';
require_once __DIR__ . '/config.php';

use Longman\TelegramBot\Telegram;
use Longman\TelegramBot\Exception\TelegramException;
use MyBot\Handlers\StartHandler;
use MyBot\Handlers\AstatkaHandler;
use MyBot\Handlers\AIGenerateHandler;
use MyBot\Handlers\GeneralQuestionsHandler;
use MyBot\Handlers\ModelImagesHandler;
use MyBot\Handlers\ModelPricesHandler;
use MyBot\Handlers\AdminHandler;
use MyBot\Services\AdminStorage;

try {
    // Create Telegram API object
    $telegram = new Telegram(BOT_TOKEN, 'mybot');
    
    // Load admins from admins.json
    AdminStorage::loadAdmins();
    
    // Register command handlers
    $telegram->addCommandClass(StartHandler::class);
    $telegram->addCommandClass(AstatkaHandler::class);
    $telegram->addCommandClass(AIGenerateHandler::class);
    $telegram->addCommandClass(GeneralQuestionsHandler::class);
    $telegram->addCommandClass(ModelImagesHandler::class);
    $telegram->addCommandClass(ModelPricesHandler::class);
    $telegram->addCommandClass(AdminHandler::class);
    
    // Handle webhook or polling
    if (php_sapi_name() === 'cli') {
        // Long polling
        $telegram->enableLimiter();
        while (true) {
            $telegram->handleGetUpdates();
            sleep(1);
        }
    } else {
        // Webhook
        $telegram->handle();
    }
    
} catch (TelegramException $e) {
    error_log($e->getMessage());
}

