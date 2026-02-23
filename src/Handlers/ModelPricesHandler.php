<?php
/**
 * Model Prices Handler
 */

namespace MyBot\Handlers;

use Longman\TelegramBot\Commands\SystemCommand;
use Longman\TelegramBot\Entities\InlineKeyboard;
use Longman\TelegramBot\Entities\InlineKeyboardButton;
use Longman\TelegramBot\Request;
use MyBot\Services\GoogleSheetService;
use MyBot\Services\Settings;
use MyBot\Utils\ProductUtils;

class ModelPricesHandler extends SystemCommand
{
    protected $name = 'model_prices';
    protected $description = 'Model prices handler';
    
    public function execute()
    {
        $callbackQuery = $this->getCallbackQuery();
        if ($callbackQuery) {
            return $this->handleCallback($callbackQuery);
        }
        
        $message = $this->getMessage();
        if ($message) {
            return $this->handleMessage($message);
        }
        
        return Request::emptyResponse();
    }
    
    private function handleCallback($callbackQuery)
    {
        $data = $callbackQuery->getData();
        $chatId = $callbackQuery->getMessage()->getChat()->getId();
        $messageId = $callbackQuery->getMessage()->getMessageId();
        $userId = $callbackQuery->getFrom()->getId();
        
        // Check access
        if (!Settings::hasPriceAccess($userId) && !\MyBot\Services\AdminStorage::isAnyAdmin($userId)) {
            return Request::answerCallbackQuery([
                'callback_query_id' => $callbackQuery->getId(),
                'text' => '❌ Sizda bu funksiyaga kirish huquqi yo\'q.',
                'show_alert' => true
            ]);
        }
        
        if ($data === 'menu_model_prices') {
            return $this->showMenu($chatId, $messageId);
        }
        
        if ($data === 'prices_general') {
            return $this->startGeneral($chatId, $messageId);
        }
        
        return Request::emptyResponse();
    }
    
    private function handleMessage($message)
    {
        $chatId = $message->getChat()->getId();
        $userId = $message->getFrom()->getId();
        $text = $message->getText();
        
        // Check access
        if (!Settings::hasPriceAccess($userId) && !\MyBot\Services\AdminStorage::isAnyAdmin($userId)) {
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => '❌ Sizda bu funksiyaga kirish huquqi yo\'q.'
            ]);
        }
        
        if (empty($text)) {
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Iltimos, to'g'ri mahsulot kodini yuboring."
            ]);
        }
        
        try {
            $sheetService = new GoogleSheetService();
            $prices = $sheetService->readPricesFromSheets3();
            
            $userCodeNorm = ProductUtils::normalizeCode($text);
            $matched = [];
            
            foreach ($prices as $price) {
                $priceCodeNorm = $price['code_normalized'] ?? '';
                if ($priceCodeNorm === $userCodeNorm) {
                    $matched[] = $price;
                }
            }
            
            if (empty($matched)) {
                return Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => "❌ Bu mahsulot uchun narx topilmadi.",
                    'reply_markup' => $this->makeBackKeyboard()
                ]);
            }
            
            $price = $matched[0];
            $text = "💰 <b>Model narxi</b>\n\n";
            $text .= "🔹 Model nomi: " . ($price['model_name'] ?? 'N/A') . "\n";
            $text .= "🔸 Kod: " . ($price['code'] ?? 'N/A') . "\n";
            
            if (!empty($price['asosiy_price'])) {
                $text .= "• Asosiy: " . $price['asosiy_price'] . "\n";
            }
            if (!empty($price['mini_price'])) {
                $text .= "• Mini: " . $price['mini_price'] . "\n";
            }
            if (!empty($price['kasetniy_price'])) {
                $text .= "• Kasetniy: " . $price['kasetniy_price'] . "\n";
            }
            
            if (!empty($price['izoh'])) {
                $text .= "\n📝 Izoh: " . $price['izoh'];
            }
            
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $this->makeBackKeyboard(),
                'parse_mode' => 'HTML'
            ]);
            
        } catch (\Exception $e) {
            error_log("Error getting price: " . $e->getMessage());
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Xatolik yuz berdi: " . $e->getMessage()
            ]);
        }
    }
    
    private function showMenu($chatId, $messageId)
    {
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '📦 Umumiy narxdan bilish', 'callback_data' => 'prices_general'])],
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'menu_main'])]
        ]);
        
        $text = "💰 <b>Modellar narxini bilish</b>\n\nQuyidagi bo'limlardan birini tanlang:";
        
        try {
            Request::editMessageText([
                'chat_id' => $chatId,
                'message_id' => $messageId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
        } catch (\Exception $e) {
            Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
        }
        
        return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
    }
    
    private function startGeneral($chatId, $messageId)
    {
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'price_back'])]
        ]);
        
        $text = "📦 <b>Umumiy narxdan bilish</b>\n\nMahsulot kodini yuboring:";
        
        try {
            Request::editMessageText([
                'chat_id' => $chatId,
                'message_id' => $messageId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
        } catch (\Exception $e) {
            Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
        }
        
        return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
    }
    
    private function makeBackKeyboard()
    {
        return new InlineKeyboard([
            [
                new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'price_back']),
                new InlineKeyboardButton(['text' => '🏠 Asosiy menyu', 'callback_data' => 'menu_main'])
            ]
        ]);
    }
}

