<?php
/**
 * General Questions Handler
 */

namespace MyBot\Handlers;

use Longman\TelegramBot\Commands\SystemCommand;
use Longman\TelegramBot\Entities\InlineKeyboard;
use Longman\TelegramBot\Entities\InlineKeyboardButton;
use Longman\TelegramBot\Request;
use MyBot\Services\AIService;
use MyBot\Handlers\StartHandler;

class GeneralQuestionsHandler extends SystemCommand
{
    protected $name = 'questions';
    protected $description = 'General questions handler';
    
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
        
        if ($data === 'menu_questions') {
            $keyboard = new InlineKeyboard([
                [new InlineKeyboardButton(['text' => '🔙 Orqaga', 'callback_data' => 'questions_back'])]
            ]);
            
            $text = "❓ Savolingizni yozing.";
            
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
        
        if ($data === 'questions_back') {
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
    
    private function handleMessage($message)
    {
        $chatId = $message->getChat()->getId();
        $text = $message->getText();
        
        if (empty($text)) {
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Savolingizni matn ko'rinishida yuboring."
            ]);
        }
        
        // Send "Ishlanmoqda..." message
        $waitingMsg = Request::sendMessage([
            'chat_id' => $chatId,
            'text' => '⏳ Savolingiz ishlanmoqda. Iltimos kuting...'
        ]);
        
        try {
            $aiService = new AIService();
            $answer = $aiService->generateAnswer($text);
            
            // Delete waiting message
            if ($waitingMsg->isOk()) {
                Request::deleteMessage([
                    'chat_id' => $chatId,
                    'message_id' => $waitingMsg->getResult()->getMessageId()
                ]);
            }
            
            $keyboard = new InlineKeyboard([
                [new InlineKeyboardButton(['text' => '🔙 Orqaga', 'callback_data' => 'questions_back'])]
            ]);
            
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $answer,
                'reply_markup' => $keyboard
            ]);
            
        } catch (\Exception $e) {
            error_log("Error processing question: " . $e->getMessage());
            
            // Delete waiting message
            if ($waitingMsg->isOk()) {
                Request::deleteMessage([
                    'chat_id' => $chatId,
                    'message_id' => $waitingMsg->getResult()->getMessageId()
                ]);
            }
            
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Xatolik yuz berdi. Qayta urinib ko'ring."
            ]);
        }
    }
}

