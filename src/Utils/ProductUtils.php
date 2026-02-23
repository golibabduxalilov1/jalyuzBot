<?php
/**
 * Product Utility Functions
 */

namespace MyBot\Utils;

class ProductUtils
{
    /**
     * Normalize product code
     */
    public static function normalizeCode($code)
    {
        if (empty($code)) {
            return '';
        }
        
        $normalized = strtoupper(trim($code));
        $normalized = preg_replace('/\s+/', '', $normalized);
        $normalized = preg_replace('/[^A-Z0-9]/', '', $normalized);
        
        return $normalized;
    }
    
    /**
     * Parse quantity string to float
     */
    public static function parseQuantity($quantityStr)
    {
        if (empty($quantityStr)) {
            return 0.0;
        }
        
        $cleaned = trim($quantityStr);
        $cleaned = preg_replace('/\s*(kv|кв|m2|m²|sq|sqm)\s*$/i', '', $cleaned);
        $cleaned = preg_replace('/[^\d.,\-]/', '', $cleaned);
        $cleaned = str_replace(',', '.', $cleaned);
        
        return (float)$cleaned;
    }
    
    /**
     * Format quantity for display
     */
    public static function formatQuantity($quantity)
    {
        if (is_null($quantity)) {
            return '0 kv';
        }
        
        $qtyFloat = (float)$quantity;
        if ($qtyFloat == (int)$qtyFloat) {
            return (int)$qtyFloat . ' kv';
        } else {
            return number_format($qtyFloat, 2, '.', '') . ' kv';
        }
    }
    
    /**
     * Normalize collection name for comparison
     */
    public static function normalizeCollection($value)
    {
        if (empty($value)) {
            return '';
        }
        
        $value = strtolower(trim($value));
        
        // Basic Cyrillic to Latin conversion
        $cyrillicToLatin = [
            'а' => 'a', 'б' => 'b', 'в' => 'v', 'г' => 'g', 'д' => 'd',
            'е' => 'e', 'ё' => 'e', 'ж' => 'zh', 'з' => 'z', 'и' => 'i',
            'й' => 'y', 'к' => 'k', 'л' => 'l', 'м' => 'm', 'н' => 'n',
            'о' => 'o', 'п' => 'p', 'р' => 'r', 'с' => 's', 'т' => 't',
            'у' => 'u', 'ф' => 'f', 'х' => 'h', 'ц' => 'ts', 'ч' => 'ch',
            'ш' => 'sh', 'щ' => 'sch', 'ъ' => '', 'ы' => 'y', 'ь' => '',
            'э' => 'e', 'ю' => 'yu', 'я' => 'ya'
        ];
        
        $result = '';
        for ($i = 0; $i < mb_strlen($value, 'UTF-8'); $i++) {
            $char = mb_substr($value, $i, 1, 'UTF-8');
            $result .= $cyrillicToLatin[$char] ?? $char;
        }
        
        $value = $result;
        $value = preg_replace('/[\s\t\n\r]/', '', $value);
        $value = str_replace(['-', '_', '.', ','], '', $value);
        $value = preg_replace('/[^a-z0-9]/', '', $value);
        
        return $value;
    }
    
    /**
     * Check if two collections match
     */
    public static function collectionsMatch($collection1, $collection2)
    {
        // Remove parentheses and everything after from collection2
        if (strpos($collection2, '(') !== false) {
            $collection2 = trim(explode('(', $collection2)[0]);
        }
        
        $norm1 = self::normalizeCollection($collection1);
        $norm2 = self::normalizeCollection($collection2);
        
        if (empty($norm1) || empty($norm2)) {
            return false;
        }
        
        return $norm1 === $norm2;
    }
}

