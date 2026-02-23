<?php
/**
 * AI Generate Handler
 */

namespace MyBot\Handlers;

use Longman\TelegramBot\Commands\SystemCommand;
use Longman\TelegramBot\Entities\InlineKeyboard;
use Longman\TelegramBot\Entities\InlineKeyboardButton;
use Longman\TelegramBot\Request;
use MyBot\Handlers\StartHandler;

class AIGenerateHandler extends SystemCommand
{
    protected $name = 'ai_generate';
    protected $description = 'AI Generate handler';
    
    public function execute()
    {
        $callbackQuery = $this->getCallbackQuery();
        if ($callbackQuery) {
            return $this->handleCallback($callbackQuery);
        }
        
        return Request::emptyResponse();
    }
    
    private function handleCallback($callbackQuery)
    {
        $data = $callbackQuery->getData();
        $chatId = $callbackQuery->getMessage()->getChat()->getId();
        $messageId = $callbackQuery->getMessage()->getMessageId();
        
        if ($data === 'menu_ai_generate') {
            $text = "🚧 AI generatsiya funksiyasi vaqtincha ishlamayapti.\n"
                  . "Tez orada to'liq ishga tushiriladi.\n\n"
                  . "⬅️ Iltimos, asosiy menyuga qayting.";
            
            $keyboard = new InlineKeyboard([
                [new InlineKeyboardButton(['text' => '🔙 Orqaga', 'callback_data' => 'ai_generate_back'])]
            ]);
            
            try {
                Request::editMessageText([
                    'chat_id' => $chatId,
                    'message_id' => $messageId,
                    'text' => $text,
                    'reply_markup' => $keyboard
                ]);
            } catch (\Exception $e) {
                Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => $text,
                    'reply_markup' => $keyboard
                ]);
            }
            
            return Request::answerCallbackQuery(['callback_query_id' => $callbackQuery->getId()]);
        }
        
        if ($data === 'ai_generate_back') {
            $keyboard = StartHandler::makeMainMenuKeyboard($callbackQuery->getFrom()->getId());
            $text = "👋 Assalomu alaykum! Botga xush kelibsiz.\n\n"
                  . "Quyidagi menyulardan birini tanlang:";
            
            Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            
            return Request::answerCallbackQuery(['callback_query_id' => $callbackQuery->getId()]);
        }
        
        return Request::emptyResponse();
    }
}

