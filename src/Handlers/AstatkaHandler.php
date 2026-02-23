<?php
/**
 * Astatka Handler
 */

namespace MyBot\Handlers;

use Longman\TelegramBot\Commands\SystemCommand;
use Longman\TelegramBot\Entities\InlineKeyboard;
use Longman\TelegramBot\Entities\InlineKeyboardButton;
use Longman\TelegramBot\Request;
use MyBot\Services\GoogleSheetService;
use MyBot\Services\Settings;
use MyBot\Utils\ProductUtils;
use MyBot\Handlers\StartHandler;

class AstatkaHandler extends SystemCommand
{
    protected $name = 'astatka';
    protected $description = 'Astatka handler';
    
    private static $menuMessages = [];
    private static $resultMessages = [];
    private static $errorMessages = [];
    
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
            case 'menu_astatka':
                return $this->showAstatkaMenu($chatId, $messageId);
            case 'astatka_general':
                return $this->startGeneralAstatka($chatId, $messageId);
            case 'astatka_collection':
                return $this->showCollectionMenu($chatId, $messageId);
            case 'astatka_similar_codes':
                return $this->startSimilarCodes($chatId, $messageId);
            case 'astatka_back':
                return $this->goBack($chatId, $messageId);
            default:
                if (strpos($data, 'collection_') === 0) {
                    $collection = substr($data, 11);
                    return $this->handleCollectionSelection($chatId, $collection, $callbackQuery->getFrom()->getId());
                }
        }
        
        return Request::answerCallbackQuery(['callback_query_id' => $callbackQuery->getId()]);
    }
    
    private function handleMessage($message)
    {
        $chatId = $message->getChat()->getId();
        $userId = $message->getFrom()->getId();
        $text = $message->getText();
        
        // Check if user is blocked
        if (Settings::isUserBlocked($userId)) {
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => '❌ Siz bloklangan foydalanuvchisiz.'
            ]);
        }
        
        // Check user limit
        if (!Settings::incrementUserRequestCount($userId)) {
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Kunlik so'rovlar limitiga yetdingiz. Ertaga qayta urinib ko'ring."
            ]);
        }
        
        // Delete user message
        Request::deleteMessage([
            'chat_id' => $chatId,
            'message_id' => $message->getMessageId()
        ]);
        
        // Delete menu message
        $this->deleteMenuMessage($chatId);
        
        // Process product code
        $userCode = ProductUtils::normalizeCode($text);
        if (empty($userCode)) {
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Iltimos, to'g'ri mahsulot kodini yuboring."
            ]);
        }
        
        try {
            $sheetService = new GoogleSheetService();
            $productData = $sheetService->getProductData($text);
            
            if (!$productData || empty($productData['matched_rows'])) {
                $errorText = $this->getErrorMessage($text);
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
            
            return $this->sendProductResult($chatId, $productData, $text, $sheetService);
            
        } catch (\Exception $e) {
            error_log("Error processing product code: " . $e->getMessage());
            return Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Xatolik yuz berdi: " . $e->getMessage() . "\n\nIltimos, qayta urinib ko'ring."
            ]);
        }
    }
    
    private function showAstatkaMenu($chatId, $messageId)
    {
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '📊 Umumiy qoldiqdan bilish', 'callback_data' => 'astatka_general'])],
            [new InlineKeyboardButton(['text' => '📁 Kolleksiya bo\'yicha bilish', 'callback_data' => 'astatka_collection'])],
            [new InlineKeyboardButton(['text' => '🔍 O\'xshash kodlar bo\'yicha qoldiqni bilish', 'callback_data' => 'astatka_similar_codes'])],
            [new InlineKeyboardButton(['text' => '🔙 Orqaga', 'callback_data' => 'BACK'])],
        ]);
        
        $text = "🟩 <b>Astatka</b>\n\nQanday usulda qidirmoqchisiz?";
        
        try {
            $result = Request::editMessageText([
                'chat_id' => $chatId,
                'message_id' => $messageId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            
            if ($result->isOk()) {
                self::$menuMessages[$chatId] = $messageId;
            }
        } catch (\Exception $e) {
            $this->deleteMenuMessage($chatId);
            $result = Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            
            if ($result->isOk()) {
                self::$menuMessages[$chatId] = $result->getResult()->getMessageId();
            }
        }
        
        return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
    }
    
    private function startGeneralAstatka($chatId, $messageId)
    {
        $this->deleteErrorMessage($chatId);
        
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '🔙 Orqaga', 'callback_data' => 'menu_astatka'])],
            [new InlineKeyboardButton(['text' => '🔙 Asosiy menyu', 'callback_data' => 'menu_main'])],
        ]);
        
        $text = "📊 <b>Umumiy qoldiqdan bilish</b>\n\nMahsulot kodini yuboring:";
        
        try {
            Request::editMessageText([
                'chat_id' => $chatId,
                'message_id' => $messageId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            self::$menuMessages[$chatId] = $messageId;
        } catch (\Exception $e) {
            $this->deleteMenuMessage($chatId);
            $result = Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            if ($result->isOk()) {
                self::$menuMessages[$chatId] = $result->getResult()->getMessageId();
            }
        }
        
        return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
    }
    
    private function showCollectionMenu($chatId, $messageId)
    {
        $this->deleteErrorMessage($chatId);
        
        $collections = [
            '0-start', '1-stage', '2-middle', '3-optimal', '4-top', '5-perfect', '6-exclusive',
            'Плиссе 1-коллекция', 'Плиссе 2-коллекция', 'Плиссе 3-коллекция', 'Плиссе 4-коллекция',
            'Турк лента 1 (ески турклар)', 'Турк лента 2 (йанги турклар)',
            'Ролло штор', 'Дикей'
        ];
        
        $buttons = [];
        foreach ($collections as $collection) {
            $buttons[] = [new InlineKeyboardButton([
                'text' => $collection,
                'callback_data' => 'collection_' . $collection
            ])];
        }
        
        $buttons[] = [new InlineKeyboardButton(['text' => '🔙 Orqaga', 'callback_data' => 'menu_astatka'])];
        $buttons[] = [new InlineKeyboardButton(['text' => '🔙 Asosiy menyu', 'callback_data' => 'menu_main'])];
        
        $keyboard = new InlineKeyboard($buttons);
        
        $text = "📁 <b>Kolleksiya bo'yicha bilish</b>\n\nKolleksiyani tanlang:";
        
        try {
            Request::editMessageText([
                'chat_id' => $chatId,
                'message_id' => $messageId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            self::$menuMessages[$chatId] = $messageId;
        } catch (\Exception $e) {
            $this->deleteMenuMessage($chatId);
            $result = Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            if ($result->isOk()) {
                self::$menuMessages[$chatId] = $result->getResult()->getMessageId();
            }
        }
        
        return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
    }
    
    private function handleCollectionSelection($chatId, $collection, $userId)
    {
        // Check if user is blocked
        if (Settings::isUserBlocked($userId)) {
            return Request::answerCallbackQuery([
                'callback_query_id' => $this->getCallbackQuery()->getId(),
                'text' => '❌ Siz bloklangan foydalanuvchisiz.',
                'show_alert' => true
            ]);
        }
        
        // Check user limit
        if (!Settings::incrementUserRequestCount($userId)) {
            return Request::answerCallbackQuery([
                'callback_query_id' => $this->getCallbackQuery()->getId(),
                'text' => "❌ Kunlik so'rovlar limitiga yetdingiz.",
                'show_alert' => true
            ]);
        }
        
        $this->deleteMenuMessage($chatId);
        
        try {
            $sheetService = new GoogleSheetService();
            $products = $sheetService->readProducts();
            
            $matched = [];
            foreach ($products as $p) {
                $rowCollection = $p['collection'] ?? '';
                if (ProductUtils::collectionsMatch($rowCollection, $collection)) {
                    $matched[] = $p;
                }
            }
            
            if (empty($matched)) {
                $errorText = $this->getErrorMessage($collection);
                $result = Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => $errorText,
                    'reply_markup' => $this->makeBackKeyboard()
                ]);
                
                if ($result->isOk()) {
                    self::$errorMessages[$chatId] = $result->getResult()->getMessageId();
                }
                return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
            }
            
            $dateValue = $matched[0]['date'] ?? 'N/A';
            $lines = [];
            foreach ($matched as $p) {
                $codeDisp = $p['code_original'] ?? $p['code'] ?? '';
                $qty = $p['quantity'] ?? '';
                $qtyStr = is_null($qty) ? '' : (string)$qty;
                $lines[] = "- {$codeDisp} — {$qtyStr}";
            }
            
            $header = "📁 Kolleksiya: {$collection}\n📅 Sana: {$dateValue}\n\nTopilgan mahsulotlar:\n\n";
            $text = $header . implode("\n", $lines);
            
            // Check message length limit (4096)
            if (strlen($text) > 4096) {
                $chunks = $this->splitLongMessage($header, $lines, 4000);
                $first = true;
                foreach ($chunks as $chunk) {
                    if ($first) {
                        $result = Request::sendMessage([
                            'chat_id' => $chatId,
                            'text' => $chunk,
                            'reply_markup' => $this->makeBackKeyboard()
                        ]);
                        $first = false;
                    } else {
                        Request::sendMessage(['chat_id' => $chatId, 'text' => $chunk]);
                    }
                }
            } else {
                $result = Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => $text,
                    'reply_markup' => $this->makeBackKeyboard()
                ]);
            }
            
            if ($result && $result->isOk()) {
                self::$resultMessages[$chatId] = $result->getResult()->getMessageId();
            }
            
        } catch (\Exception $e) {
            error_log("Error processing collection: " . $e->getMessage());
            Request::sendMessage([
                'chat_id' => $chatId,
                'text' => "❌ Xatolik yuz berdi: " . $e->getMessage()
            ]);
        }
        
        return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
    }
    
    private function startSimilarCodes($chatId, $messageId)
    {
        $this->deleteErrorMessage($chatId);
        
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '🔙 Orqaga', 'callback_data' => 'menu_astatka'])],
            [new InlineKeyboardButton(['text' => '🔙 Asosiy menyu', 'callback_data' => 'menu_main'])],
        ]);
        
        $text = "🔍 <b>O'xshash kodlar bo'yicha qoldiqni bilish</b>\n\nKod yuboring:";
        
        try {
            Request::editMessageText([
                'chat_id' => $chatId,
                'message_id' => $messageId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            self::$menuMessages[$chatId] = $messageId;
        } catch (\Exception $e) {
            $this->deleteMenuMessage($chatId);
            $result = Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $keyboard,
                'parse_mode' => 'HTML'
            ]);
            if ($result->isOk()) {
                self::$menuMessages[$chatId] = $result->getResult()->getMessageId();
            }
        }
        
        return Request::answerCallbackQuery(['callback_query_id' => $this->getCallbackQuery()->getId()]);
    }
    
    private function sendProductResult($chatId, $productData, $userRaw, $sheetService)
    {
        $matchedRows = $productData['matched_rows'];
        $collection = $productData['collection'] ?? 'N/A';
        $date = $productData['date'] ?? 'N/A';
        $originalCode = $productData['original_code'] ?? $userRaw;
        
        // Get model name from sheets3
        $modelName = '';
        try {
            $prices = $sheetService->readPricesFromSheets3();
            $userCodeNorm = ProductUtils::normalizeCode($userRaw);
            foreach ($prices as $price) {
                $priceCodeNorm = ProductUtils::normalizeCode($price['code'] ?? '');
                if ($priceCodeNorm === $userCodeNorm) {
                    $modelName = trim($price['model_name'] ?? '');
                    break;
                }
            }
        } catch (\Exception $e) {
            // Ignore
        }
        
        // Get image URL
        $imageUrl = '';
        if (!empty($matchedRows)) {
            $imageUrl = $matchedRows[0]['image_url'] ?? '';
            if (empty($imageUrl)) {
                foreach ($matchedRows as $row) {
                    $img = $row['image_url'] ?? '';
                    if (!empty($img)) {
                        $imageUrl = $img;
                        break;
                    }
                }
            }
        }
        
        if (empty($imageUrl)) {
            $imageUrl = DEFAULT_IMAGE_URL;
        }
        
        if (!empty($imageUrl)) {
            $imageUrl = $sheetService->convertGoogleDriveLink($imageUrl);
        }
        
        // Group by collection
        $asosiyQoldiq = [];
        $miniQoldiq = [];
        $kasetniyQoldiq = [];
        
        foreach ($matchedRows as $row) {
            $codeDisp = $row['code_original'] ?? $row['code'] ?? $originalCode;
            $qty = $row['quantity'] ?? '';
            $qtyStr = is_null($qty) ? '' : (string)$qty;
            
            $rowCollection = strtolower(trim($row['collection'] ?? ''));
            
            if (strpos($rowCollection, 'asosiy') !== false || strpos($rowCollection, 'основной') !== false) {
                $asosiyQoldiq[] = [$codeDisp, $qtyStr];
            } elseif (strpos($rowCollection, 'mini') !== false || strpos($rowCollection, 'мини') !== false) {
                $miniQoldiq[] = [$codeDisp, $qtyStr];
            } elseif (strpos($rowCollection, 'kasetniy') !== false || strpos($rowCollection, 'кассетный') !== false || strpos($rowCollection, 'kaset') !== false) {
                $kasetniyQoldiq[] = [$codeDisp, $qtyStr];
            } else {
                $asosiyQoldiq[] = [$codeDisp, $qtyStr];
            }
        }
        
        // Format text
        $header = "🔹 Model nomi: " . ($modelName ?: $originalCode) . "\n\n";
        $header .= "🔸 Model kodi: {$originalCode}\n";
        
        $modelNameUpper = strtoupper($modelName);
        $isDikey = (strpos($modelNameUpper, 'ДИКИЙ') !== false || strpos($modelNameUpper, 'ДИКЕЙ') !== false || 
                   strpos($modelNameUpper, 'DIKEY') !== false);
        
        if (!$isDikey) {
            $header .= "📂 Kolleksiya: {$collection}\n";
        }
        $header .= "📅 Sana: {$date}\n\nQoldiq:\n";
        
        // Format based on model type
        if (strpos($modelNameUpper, 'ROLLO SHTOR') !== false || strpos($modelNameUpper, 'РОЛЛО ШТОР') !== false) {
            if (!empty($asosiyQoldiq)) {
                foreach ($asosiyQoldiq as $item) {
                    $header .= "• 50%lik: {$item[1]}\n";
                }
            }
            if (!empty($miniQoldiq)) {
                foreach ($miniQoldiq as $item) {
                    $header .= "• 100%lik: {$item[1]}\n";
                }
            }
        } elseif ($isDikey) {
            if (!empty($asosiyQoldiq)) {
                foreach ($asosiyQoldiq as $item) {
                    $header .= "• To'ldi uzi bo'lsa: {$item[1]}\n";
                }
            }
            if (!empty($miniQoldiq)) {
                foreach ($miniQoldiq as $item) {
                    $header .= "• Yoniga porter bo'lsa: {$item[1]}\n";
                }
            }
        } elseif (strpos($modelNameUpper, 'PLISE') !== false || strpos($modelNameUpper, 'ПЛИСЕ') !== false) {
            if (!empty($asosiyQoldiq)) {
                foreach ($asosiyQoldiq as $item) {
                    $header .= "• 0,50 kv: {$item[1]}\n";
                }
            }
            if (!empty($miniQoldiq)) {
                foreach ($miniQoldiq as $item) {
                    $header .= "• 1,00 kv: {$item[1]}\n";
                }
            }
        } else {
            if (!empty($asosiyQoldiq)) {
                $header .= "• Asosiy:\n";
                foreach ($asosiyQoldiq as $item) {
                    $header .= "  - {$item[0]} — {$item[1]}\n";
                }
            }
            if (!empty($miniQoldiq)) {
                $header .= "• Mini:\n";
                foreach ($miniQoldiq as $item) {
                    $header .= "  - {$item[0]} — {$item[1]}\n";
                }
            }
            if (!empty($kasetniyQoldiq)) {
                $header .= "• Kasetniy:\n";
                foreach ($kasetniyQoldiq as $item) {
                    $header .= "  - {$item[0]} — {$item[1]}\n";
                }
            }
        }
        
        $text = $header;
        
        // Send result
        if (!empty($imageUrl)) {
            try {
                $result = Request::sendPhoto([
                    'chat_id' => $chatId,
                    'photo' => $imageUrl,
                    'caption' => $text,
                    'reply_markup' => $this->makeBackKeyboard(),
                    'parse_mode' => 'HTML'
                ]);
            } catch (\Exception $e) {
                $result = Request::sendMessage([
                    'chat_id' => $chatId,
                    'text' => $text,
                    'reply_markup' => $this->makeBackKeyboard(),
                    'parse_mode' => 'HTML'
                ]);
            }
        } else {
            $result = Request::sendMessage([
                'chat_id' => $chatId,
                'text' => $text,
                'reply_markup' => $this->makeBackKeyboard(),
                'parse_mode' => 'HTML'
            ]);
        }
        
        if ($result && $result->isOk()) {
            self::$resultMessages[$chatId] = $result->getResult()->getMessageId();
        }
        
        return $result;
    }
    
    private function goBack($chatId, $messageId)
    {
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '📊 Umumiy qoldiqdan bilish', 'callback_data' => 'astatka_general'])],
            [new InlineKeyboardButton(['text' => '📁 Kolleksiya bo\'yicha bilish', 'callback_data' => 'astatka_collection'])],
            [new InlineKeyboardButton(['text' => '🔍 O\'xshash kodlar bo\'yicha qoldiqni bilish', 'callback_data' => 'astatka_similar_codes'])],
            [new InlineKeyboardButton(['text' => '🔙 Orqaga', 'callback_data' => 'BACK'])],
        ]);
        
        $text = "🟩 <b>Astatka</b>\n\nQanday usulda qidirmoqchisiz?";
        
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
                new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'astatka_back']),
                new InlineKeyboardButton(['text' => '🏠 Asosiy menyu', 'callback_data' => 'menu_main'])
            ]
        ]);
    }
    
    private function getErrorMessage($request = '')
    {
        $contactPhone = Settings::getContactPhone();
        $errorText = "❌ Bu mahsulot topilmadi yoki kodni noto'g'ri yozdingiz.\n\n"
                   . "📞 Mahsulot omborda qolgan-qolmaganini bilish uchun:\n"
                   . "{$contactPhone}";
        
        if (!empty($request)) {
            $errorText .= "\n\n🔎 Kiritilgan so'rov: {$request}";
        }
        
        return $errorText;
    }
    
    private function deleteMenuMessage($chatId)
    {
        if (isset(self::$menuMessages[$chatId])) {
            try {
                Request::deleteMessage([
                    'chat_id' => $chatId,
                    'message_id' => self::$menuMessages[$chatId]
                ]);
            } catch (\Exception $e) {
                // Ignore
            }
            unset(self::$menuMessages[$chatId]);
        }
    }
    
    private function deleteErrorMessage($chatId)
    {
        if (isset(self::$errorMessages[$chatId])) {
            try {
                Request::deleteMessage([
                    'chat_id' => $chatId,
                    'message_id' => self::$errorMessages[$chatId]
                ]);
            } catch (\Exception $e) {
                // Ignore
            }
            unset(self::$errorMessages[$chatId]);
        }
    }
    
    private function splitLongMessage($header, $lines, $chunkSize)
    {
        $chunks = [];
        $currentChunk = $header;
        
        foreach ($lines as $line) {
            if (strlen($currentChunk) + strlen($line) + 1 > $chunkSize) {
                $chunks[] = $currentChunk;
                $currentChunk = $header;
            }
            $currentChunk .= $line . "\n";
        }
        
        if ($currentChunk !== $header) {
            $chunks[] = $currentChunk;
        }
        
        return $chunks;
    }
}

