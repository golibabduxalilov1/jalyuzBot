<?php
/**
 * Admin Handler
 */

namespace MyBot\Handlers;

use Longman\TelegramBot\Commands\SystemCommand;
use Longman\TelegramBot\Entities\InlineKeyboard;
use Longman\TelegramBot\Entities\InlineKeyboardButton;
use Longman\TelegramBot\Request;
use MyBot\Services\AdminStorage;

class AdminHandler extends SystemCommand
{
    protected $name = 'admin';
    protected $description = 'Admin handler';
    
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
        $userId = $callbackQuery->getFrom()->getId();
        
        // Check if user is admin
        if (!AdminStorage::isAnyAdmin($userId)) {
            return Request::answerCallbackQuery([
                'callback_query_id' => $callbackQuery->getId(),
                'text' => '❌ Sizda admin huquqi yo\'q.',
                'show_alert' => true
            ]);
        }
        
        if ($data === 'admin_panel') {
            return $this->showAdminPanel($chatId, $messageId);
        }
        
        if ($data === 'admin_sellers') {
            return $this->showSellersMenu($chatId, $messageId);
        }
        
        if ($data === 'admin_add_seller') {
            return $this->showAddSellerPrompt($chatId, $messageId);
        }
        
        if ($data === 'admin_remove_seller') {
            return $this->showRemoveSellerList($chatId, $messageId);
        }
        
        if ($data === 'admin_list_sellers') {
            return $this->showSellersList($chatId, $messageId);
        }
        
        if (strpos($data, 'admin_remove_seller_confirm:') === 0) {
            $sellerId = (int)str_replace('admin_remove_seller_confirm:', '', $data);
            return $this->removeSeller($chatId, $messageId, $sellerId);
        }
        
        if ($data === 'admin_partners') {
            return $this->handleAdminPartners($callbackQuery);
        }
        
        return Request::emptyResponse();
    }
    
    private function showAdminPanel($chatId, $messageId)
    {
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '👥 Adminlar', 'callback_data' => 'admin_users'])],
            [new InlineKeyboardButton(['text' => '⚙️ Sozlamalar', 'callback_data' => 'admin_settings'])],
            [new InlineKeyboardButton(['text' => '📊 Statistika', 'callback_data' => 'admin_stats'])],
            [new InlineKeyboardButton(['text' => '👤 Sotuvchilar', 'callback_data' => 'admin_sellers'])],
            [new InlineKeyboardButton(['text' => '🤝 Hamkorlar', 'callback_data' => 'admin_partners'])],
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'menu_main'])]
        ]);
        
        $text = "🧰 <b>Admin panel</b>\n\nQuyidagi bo'limlardan birini tanlang:";
        
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
    
    private function showSellersMenu($chatId, $messageId)
    {
        $sellers = AdminStorage::getSellers();
        $sellersList = [];
        foreach ($sellers as $sellerId => $sellerName) {
            $sellersList[] = "• {$sellerName} — {$sellerId}";
        }
        $sellersText = !empty($sellersList) ? implode("\n", $sellersList) : "Hozircha sotuvchilar mavjud emas";
        
        $text = "👤 <b>Sotuvchilar</b>\n────────────\n\n{$sellersText}";
        
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '➕ Sotuvchi qo\'shish', 'callback_data' => 'admin_add_seller'])],
            [new InlineKeyboardButton(['text' => '➖ Sotuvchini o\'chirish', 'callback_data' => 'admin_remove_seller'])],
            [new InlineKeyboardButton(['text' => '📋 Sotuvchilar ro\'yxati', 'callback_data' => 'admin_list_sellers'])],
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'admin_panel'])]
        ]);
        
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
    
    private function showAddSellerPrompt($chatId, $messageId)
    {
        $text = "➕ <b>Sotuvchi qo'shish</b>\n────────────\n\nSotuvchining Telegram ID yoki @username'ini yuboring:\n\n⚠️ Eslatma: ID yoki username ni to'g'ri kiriting.";
        
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'admin_sellers'])]
        ]);
        
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
        
        return Request::answerCallbackQuery([
            'callback_query_id' => $this->getCallbackQuery()->getId(),
            'text' => 'ID yoki username ni yuboring'
        ]);
    }
    
    private function showRemoveSellerList($chatId, $messageId)
    {
        $sellers = AdminStorage::getSellers();
        
        if (empty($sellers)) {
            $text = "➖ <b>Sotuvchini o'chirish</b>\n────────────\n\nHozircha sotuvchilar mavjud emas.";
            $keyboard = new InlineKeyboard([
                [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'admin_sellers'])]
            ]);
        } else {
            $text = "➖ <b>Sotuvchini o'chirish</b>\n────────────\n\nO'chirish uchun sotuvchini tanlang:";
            $keyboardButtons = [];
            foreach ($sellers as $sellerId => $sellerName) {
                $keyboardButtons[] = [new InlineKeyboardButton([
                    'text' => "❌ {$sellerName} — {$sellerId}",
                    'callback_data' => "admin_remove_seller_confirm:{$sellerId}"
                ])];
            }
            $keyboardButtons[] = [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'admin_sellers'])];
            $keyboard = new InlineKeyboard($keyboardButtons);
        }
        
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
    
    private function showSellersList($chatId, $messageId)
    {
        $sellers = AdminStorage::getSellers();
        $sellersList = [];
        foreach ($sellers as $sellerId => $sellerName) {
            $sellersList[] = "• {$sellerName} — {$sellerId}";
        }
        $sellersText = !empty($sellersList) ? implode("\n", $sellersList) : "Hozircha sotuvchilar mavjud emas";
        
        $text = "📋 <b>Sotuvchilar ro'yxati</b>\n────────────\n\n{$sellersText}";
        
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'admin_sellers'])]
        ]);
        
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
    
    private function removeSeller($chatId, $messageId, $sellerId)
    {
        $sellerName = AdminStorage::getSellerName($sellerId);
        AdminStorage::removeSeller($sellerId);
        
        $text = "❌ <b>Sotuvchi olib tashlandi</b>\n────────────\n\nSotuvchi ro'yxatdan o'chirildi:\n• {$sellerName} — {$sellerId}";
        
        $keyboard = new InlineKeyboard([
            [new InlineKeyboardButton(['text' => '⬅️ Orqaga', 'callback_data' => 'admin_sellers'])]
        ]);
        
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
        
        return Request::answerCallbackQuery([
            'callback_query_id' => $this->getCallbackQuery()->getId(),
            'text' => '✅ Sotuvchi o\'chirildi'
        ]);
    }
    
    private function handleAdminPartners($callbackQuery)
    {
        // Placeholder handler for Hamkorlar button
        return Request::answerCallbackQuery(['callback_query_id' => $callbackQuery->getId()]);
    }
}

