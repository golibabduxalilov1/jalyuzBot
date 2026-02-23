<?php
/**
 * Model Images Handler
 */

namespace MyBot\Handlers;

use Longman\TelegramBot\Commands\SystemCommand;
use Longman\TelegramBot\Entities\InlineKeyboard;
use Longman\TelegramBot\Entities\InlineKeyboardButton;
use Longman\TelegramBot\Request;
use MyBot\Services\GoogleSheetService;
use MyBot\Utils\ProductUtils;
use MyBot\Handlers\StartHandler;

class ModelImagesHandler extends SystemCommand
{
    protected $name = 'model_images';
    protected $description = 'Model images handler';
    
    private static $similarMode = []; // Track which users are in similar models mode
    private static $errorMessages = []; // Track error messages to delete them later
    
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
        
        switch ($data) {
            case 'menu_model_images':
                // Reset similar mode when returning to menu
                unset(self::$similarMode[$chatId]);
                unset(self::$errorMessages[$chatId]);
                return $this->showMenu($chatId, $messageId);
            case 'model_single':
                // Clear similar mode when switching to single
                unset(self::$similarMode[$chatId]);
                unset(self::$errorMessages[$chatId]);
                return $this->startSingle($chatId, $messageId);
            case 'model_similar':
                return $this->startSimilar($chatId, $messageId);
            case 'model_images_back':
                // Reset similar mode state
                unset(self::$similarMode[$chatId]);
                unset(self::$errorMessages[$chatId]);
                // Return to model images menu
                return $this->showMenu($chatId, $messageId);
        }
        
        return Request::emptyResponse();
    }
    
    private function handleMessage($message)
    {
        $chatId = $message->getChat()->getId();
        $text = $message->getText();
        
        // Check if user is in similar models mode
        if (isset(self::$similarMode[$chatId]) && self::$similarMode[$chatId]) {
            return $this->handleSimilarModels($message, $chatId, $text);
        }
        
        // Original single model handling
        if (empty($text)) {
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Iltimos, to'g'ri mahsulot kodini yuboring."
            ]);
        }
        
        try {
            $sheetService = new GoogleSheetService();
            $productData = $sheetService->getProductData($text);
            
            if (!$productData || empty($productData['matched_rows'])) {
                return Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => "❌ Bu mahsulot topilmadi.",
                    'reply_markup' => $this->makeBackKeyboard()
                ]);
            }
            
            // Get image URL
            $imageUrl = '';
            foreach ($productData['matched_rows'] as $row) {
                $img = $row['image_url'] ?? '';
                if (!empty($img)) {
                    $imageUrl = $img;
                    break;
                }
            }
            
            if (empty($imageUrl)) {
                $imageUrl = DEFAULT_IMAGE_URL;
            }
            
            if (!empty($imageUrl)) {
                $imageUrl = $sheetService->convertGoogleDriveLink($imageUrl);
            }
            
            if (!empty($imageUrl)) {
                return Request::sendPhoto([
                    'chat_id' => $chatId,
                    'photo' => $imageUrl,
                    'caption' => "📷 Model rasmi",
                    'reply_markup' => $this->makeBackKeyboard()
                ]);
            } else {
                return Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => "❌ Bu model uchun rasm topilmadi.",
                    'reply_markup' => $this->makeBackKeyboard()
                ]);
            }
            
        } catch (\Exception $e) {
            error_log("Error getting model image: " . $e->getMessage());
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Xatolik yuz berdi: " . $e->getMessage()
            ]);
        }
    }
    
    private function handleSimilarModels($message, $chatId, $text)
    {
        // Delete previous error message if exists
        if (isset(self::$errorMessages[$chatId])) {
            try {
                Request::deleteMessage([
                    'chat_id' => $chatId,
                    'message_id' => self::$errorMessages[$chatId]
                ]);
            } catch (\Exception $e) {
                // Ignore if message already deleted
            }
            unset(self::$errorMessages[$chatId]);
        }
        
        if (empty($text)) {
            $result = Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Kod noto'g'ri kiritildi: (bo'sh)\nIltimos, kodni to'g'ri yozib qayta yuboring.",
                'reply_markup' => $this->makeBackKeyboard()
            ]);
            if ($result->isOk()) {
                self::$errorMessages[$chatId] = $result->getResult()->getMessageId();
            }
            return $result;
        }
        
        try {
            $sheetService = new GoogleSheetService();
            $productData = $sheetService->getProductData($text);
            
            // Case 1: Code doesn't exist in database
            if (!$productData || empty($productData['matched_rows'])) {
                $errorText = "❌ Kod noto'g'ri kiritildi: {$text}\nIltimos, kodni to'g'ri yozib qayta yuboring.";
                $result = Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => $errorText,
                    'reply_markup' => $this->makeBackKeyboard()
                ]);
                if ($result->isOk()) {
                    self::$errorMessages[$chatId] = $result->getResult()->getMessageId();
                }
                return $result;
            }
            
            // Case 2: Model exists but has no images
            $hasImage = false;
            $images = [];
            foreach ($productData['matched_rows'] as $row) {
                $img = $row['image_url'] ?? '';
                if (!empty($img)) {
                    $hasImage = true;
                    $imgUrl = $sheetService->convertGoogleDriveLink($img);
                    if (!empty($imgUrl)) {
                        $images[] = $imgUrl;
                    }
                }
            }
            
            if (!$hasImage || empty($images)) {
                $errorText = "ℹ️ Bu model mavjud, lekin hozircha rasmi yo'q.\nTez orada qo'shiladi.\nIltimos, boshqa kod yuboring.";
                $result = Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => $errorText,
                    'reply_markup' => $this->makeBackKeyboard()
                ]);
                if ($result->isOk()) {
                    self::$errorMessages[$chatId] = $result->getResult()->getMessageId();
                }
                return $result;
            }
            
            // Case 3: Model exists and has images - send all similar model images
            $firstImage = true;
            foreach ($images as $imageUrl) {
                if ($firstImage) {
                    $result = Request::sendPhoto([
                        'chat_id' => $chatId,
                        'photo' => $imageUrl,
                        'caption' => "📸 O'xshash modellar",
                        'reply_markup' => $this->makeBackKeyboard()
                    ]);
                    $firstImage = false;
                } else {
                    Request::sendPhoto([
                        'chat_id' => $chatId,
                        'photo' => $imageUrl
                    ]);
                }
            }
            
            return $result ?? Request::emptyResponse();
            
        } catch (\Exception $e) {
            error_log("Error getting similar model images: " . $e->getMessage());
            $errorText = "❌ Xatolik yuz berdi: " . $e->getMessage();
            $result = Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $errorText,
                'reply_markup' => $this->makeBackKeyboard()
            ]);
            if ($result->isOk()) {
                self::$errorMessages[$chatId] = $result->getResult()->getMessageId();
            }
            return $result;
        }
    }
    
    private function showMenu($chatId, $messageId)
    {
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '🖼 Bitta model rasmi', 'callback_data' => 'model_single'])],
            [new InlineKeyboardButton(['text' => '📸 O\'xshash modelllar', 'callback_data' => 'model_similar'])],
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'model_images_back'])],
        ]);
        
        $text = "📷 Modellar rasmi\n\nQuyidagi bo'limlardan birini tanlang:";
        
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
        
        return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
    }
    
    private function startSingle($chatId, $messageId)
    {
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'model_images_back'])]
        ]);
        
        $text = "🖼 <b>Bitta model rasmi</b>\n\nMahsulot kodini yuboring:";
        
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
    
    private function startSimilar($chatId, $messageId)
    {
        // Set similar mode and clear previous errors
        self::$similarMode[$chatId] = true;
        
        // Delete previous error message if exists
        if (isset(self::$errorMessages[$chatId])) {
            try {
                Request::deleteMessage([
                    'chat_id' => $chatId,
                    'message_id' => self::$errorMessages[$chatId]
                ]);
            } catch (\Exception $e) {
                // Ignore if message already deleted
            }
            unset(self::$errorMessages[$chatId]);
        }
        
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'menu_model_images'])]
        ]);
        
        $text = "📸 <b>O'xshash modelllar</b>\n\nKod yuboring:";
        
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
                new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'model_images_back']),
                new InlineKeyboardButton(['text' => '🏠 Asosiy menyu', 'callback_data' => 'menu_main'])
            ]
        ]);
    }
}

