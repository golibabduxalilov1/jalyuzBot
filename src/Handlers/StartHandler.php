<?php
/**
 * Start Command Handler
 */

namespace MyBot\Handlers;

use Longman\TelegramBot\Commands\Command;
use Longman\TelegramBot\Entities\InlineKeyboard;
use Longman\TelegramBot\Entities\InlineKeyboardButton;
use Longman\TelegramBot\Request;
use MyBot\Services\AdminStorage;
use MyBot\Services\Settings;

class StartHandler extends Command
{
    protected $name = 'start';
    protected $description = 'Start command';
    protected $usage = '/start';
    protected $version = '1.0.0';
    
    public function execute()
    {
        $message = $this->getMessage();
        $chatId = $message->getChat()->getId();
        $userId = $message->getFrom()->getId();
        
        $keyboard = $this->makeMainMenuKeyboard($userId);
        
        $text = "👋 Assalomu alaykum! Botga xush kelibsiz.\n\n"
              . "Quyidagi menyulardan birini tanlang:";
        
        $data = [
            'chat_id' => $chatId,
            'text' => $text,
            'reply_markup' => $keyboard,
            'parse_mode' => 'HTML',
        ];
        
        return Request::sendMessage($data);
    }
    
    public static function makeMainMenuKeyboard($userId = null)
    {
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '🟩 Astatka', 'callback_data' => 'menu_astatka'])],
            [new InlineKeyboardButton(['text' => '🟥 AI Generatsiya', 'callback_data' => 'menu_ai_generate'])],
            [new InlineKeyboardButton(['text' => '❓ Savol berish', 'callback_data' => 'menu_questions'])],
            [new InlineKeyboardButton(['text' => '📷 Modellar rasmi', 'callback_data' => 'menu_model_images'])],
        ]);
        
        if ($userId !== null) {
            $isSuperAdmin = AdminStorage::isSuperAdmin($userId);
            $isAdmin = AdminStorage::isAdmin($userId);
            $isAnyAdmin = $isSuperAdmin || $isAdmin;
            $hasPrice = Settings::hasPriceAccess($userId);
            $hasDiscount = Settings::hasDiscountAccess($userId);
            $hasDiscountOnly = AdminStorage::hasDiscountAccessOnly($userId);
            
            // Skidkaga tushgan modellar
            if ($isAnyAdmin || $hasPrice || $hasDiscount) {
                $keyboard->addRow(new InlineKeyboardButton([
                    'text' => '🔥 Skidkaga tushgan modellar',
                    'callback_data' => 'prices_discount'
                ]));
            }
            
            // Modellar narxini bilish
            if ($isAnyAdmin || $hasPrice) {
                $keyboard->addRow(new InlineKeyboardButton([
                    'text' => '💰 Modellar narxini bilish',
                    'callback_data' => 'menu_model_prices'
                ]));
            }
            
            // Admin panel
            if ($isAnyAdmin) {
                $keyboard->addRow(new InlineKeyboardButton([
                    'text' => '🧰 Admin panel',
                    'callback_data' => 'admin_panel'
                ]));
            }
        }
        
        return $keyboard;
    }
}

