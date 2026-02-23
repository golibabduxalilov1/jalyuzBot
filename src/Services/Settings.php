<?php
/**
 * Settings Service
 */

namespace MyBot\Services;

class Settings
{
    private static $settings = [
        'contact_phone' => '97-310-31-11 raqamiga murojaat qiling.',
        'error_message' => "❌ Ma'lumot topilmadi. Iltimos: 97-310-31-11",
        'broadcast_enabled' => true,
        'broadcast_active_users_only' => false,
        'broadcast_skip_blocked' => true,
        'history_logging_enabled' => true,
        'daily_stats_auto_reset' => true,
        'max_logs_per_user' => 100,
        'user_request_history_enabled' => true,
        'show_user_id' => false,
        'show_first_name' => true,
        'show_username' => true,
        'blocked_users' => [],
        'user_limits' => [],
    ];
    
    public static function getContactPhone()
    {
        return self::$settings['contact_phone'];
    }
    
    public static function setContactPhone($phone)
    {
        self::$settings['contact_phone'] = $phone;
    }
    
    public static function getErrorMessage()
    {
        return self::$settings['error_message'];
    }
    
    public static function setErrorMessage($message)
    {
        self::$settings['error_message'] = $message;
    }
    
    public static function isUserBlocked($userId)
    {
        return in_array($userId, self::$settings['blocked_users']);
    }
    
    public static function blockUser($userId)
    {
        if (!in_array($userId, self::$settings['blocked_users'])) {
            self::$settings['blocked_users'][] = $userId;
        }
    }
    
    public static function unblockUser($userId)
    {
        self::$settings['blocked_users'] = array_values(array_diff(self::$settings['blocked_users'], [$userId]));
    }
    
    public static function incrementUserRequestCount($userId)
    {
        if (!isset(self::$settings['user_limits'][$userId])) {
            return true; // No limit
        }
        
        $limitInfo = &self::$settings['user_limits'][$userId];
        $today = date('Y-m-d');
        
        if ($limitInfo['reset_date'] !== $today) {
            $limitInfo['current_count'] = 0;
            $limitInfo['reset_date'] = $today;
        }
        
        $limitInfo['current_count']++;
        
        return $limitInfo['current_count'] <= $limitInfo['daily_limit'];
    }
    
    public static function setUserLimit($userId, $dailyLimit)
    {
        self::$settings['user_limits'][$userId] = [
            'daily_limit' => $dailyLimit,
            'current_count' => 0,
            'reset_date' => date('Y-m-d'),
        ];
    }
    
    public static function hasPriceAccess($userId)
    {
        return \MyBot\Services\AdminStorage::hasPriceAccess($userId);
    }
    
    public static function hasDiscountAccess($userId)
    {
        return \MyBot\Services\AdminStorage::hasDiscountAccess($userId);
    }
}

