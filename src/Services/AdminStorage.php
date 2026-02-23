<?php
/**
 * Admin Storage Management
 */

namespace MyBot\Services;

class AdminStorage
{
    private static $adminsFile;
    private static $superAdmins = [];
    private static $admins = [];
    private static $priceAccess = [];
    private static $discountAccess = [];
    private static $sellers = [];
    
    public static function init()
    {
        self::$adminsFile = BASE_DIR . '/admins.json';
    }
    
    public static function loadAdmins()
    {
        self::init();
        
        if (file_exists(self::$adminsFile)) {
            try {
                $data = json_decode(file_get_contents(self::$adminsFile), true);
                self::$superAdmins = $data['super_admins'] ?? $data['main_admins'] ?? [];
                self::$admins = $data['admins'] ?? $data['helper_admins'] ?? $data['helpers'] ?? [];
                self::$priceAccess = $data['price_access'] ?? [];
                self::$discountAccess = $data['discount_access'] ?? [];
                self::$sellers = $data['sellers'] ?? [];
            } catch (\Exception $e) {
                error_log("Error loading admins.json: " . $e->getMessage());
                self::$superAdmins = [];
                self::$admins = [];
                self::$priceAccess = [];
                self::$discountAccess = [];
                self::$sellers = [];
            }
        } else {
            self::saveAdmins();
        }
    }
    
    public static function saveAdmins()
    {
        $data = [
            'main_admins' => self::$superAdmins,
            'helper_admins' => self::$admins,
            'price_access' => self::$priceAccess,
            'discount_access' => self::$discountAccess,
            'sellers' => self::$sellers,
        ];
        
        file_put_contents(self::$adminsFile, json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    }
    
    public static function isSuperAdmin($userId)
    {
        return isset(self::$superAdmins[(string)$userId]);
    }
    
    public static function isAdmin($userId)
    {
        return isset(self::$admins[(string)$userId]);
    }
    
    public static function isAnyAdmin($userId)
    {
        return self::isSuperAdmin($userId) || self::isAdmin($userId);
    }
    
    public static function hasPriceAccess($userId)
    {
        $userIdStr = (string)$userId;
        return isset(self::$superAdmins[$userIdStr]) ||
               isset(self::$admins[$userIdStr]) ||
               isset(self::$priceAccess[$userIdStr]);
    }
    
    public static function hasDiscountAccess($userId)
    {
        $userIdStr = (string)$userId;
        return isset(self::$superAdmins[$userIdStr]) ||
               isset(self::$admins[$userIdStr]) ||
               isset(self::$priceAccess[$userIdStr]) ||
               isset(self::$discountAccess[$userIdStr]);
    }
    
    public static function hasDiscountAccessOnly($userId)
    {
        $userIdStr = (string)$userId;
        return !self::isAnyAdmin($userId) &&
               !isset(self::$priceAccess[$userIdStr]) &&
               isset(self::$discountAccess[$userIdStr]);
    }
    
    public static function addSuperAdmin($userId, $name)
    {
        self::$superAdmins[(string)$userId] = trim($name);
        self::saveAdmins();
    }
    
    public static function addAdmin($userId, $name)
    {
        self::$admins[(string)$userId] = trim($name);
        self::saveAdmins();
    }
    
    public static function addPriceAccess($userId, $name)
    {
        self::$priceAccess[(string)$userId] = trim($name);
        self::saveAdmins();
    }
    
    public static function addDiscountAccess($userId, $name)
    {
        self::$discountAccess[(string)$userId] = trim($name);
        self::saveAdmins();
    }
    
    public static function removeSuperAdmin($userId)
    {
        unset(self::$superAdmins[(string)$userId]);
        self::saveAdmins();
    }
    
    public static function removeAdmin($userId)
    {
        unset(self::$admins[(string)$userId]);
        self::saveAdmins();
    }
    
    public static function removePriceAccess($userId)
    {
        unset(self::$priceAccess[(string)$userId]);
        self::saveAdmins();
    }
    
    public static function removeDiscountAccess($userId)
    {
        unset(self::$discountAccess[(string)$userId]);
        self::saveAdmins();
    }
    
    // ==================== SELLERS ====================
    
    public static function getSellers()
    {
        return self::$sellers;
    }
    
    public static function addSeller($userId, $name)
    {
        self::$sellers[(string)$userId] = trim($name);
        self::saveAdmins();
    }
    
    public static function removeSeller($userId)
    {
        unset(self::$sellers[(string)$userId]);
        self::saveAdmins();
    }
    
    public static function getSellerName($userId)
    {
        return self::$sellers[(string)$userId] ?? "User {$userId}";
    }
    
    public static function isSeller($userId)
    {
        return isset(self::$sellers[(string)$userId]);
    }
}

