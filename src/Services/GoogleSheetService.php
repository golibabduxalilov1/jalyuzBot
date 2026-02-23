<?php
/**
 * Google Sheets Service
 */

namespace MyBot\Services;

use Google_Client;
use Google_Service_Sheets;
use Google_Service_Sheets_ValueRange;
use MyBot\Utils\ProductUtils;

class GoogleSheetService
{
    private $client;
    private $service;
    private $sheetId;
    private $serviceAccountFile;
    private $productsSheetName = 'sheets1';
    private $imagesSheetName = 'sheets2';
    private $pricesSheetName = 'sheets3';
    private $discountSheetName = 'sheets4';
    
    private $recordsCache = null;
    private $cacheTimestamp = 0;
    private $cacheTtl = 300; // 5 minutes
    
    private $imagesCache = null;
    private $imagesCacheTimestamp = 0;
    
    public function __construct()
    {
        $this->sheetId = GOOGLE_SHEET_ID;
        $this->serviceAccountFile = BASE_DIR . '/' . SERVICE_ACCOUNT_FILE;
        
        $this->connect();
    }
    
    private function connect()
    {
        if (!file_exists($this->serviceAccountFile)) {
            throw new \Exception("Service account file not found: {$this->serviceAccountFile}");
        }
        
        $client = new Google_Client();
        $client->setAuthConfig($this->serviceAccountFile);
        $client->addScope(Google_Service_Sheets::SPREADSHEETS_READONLY);
        $client->addScope('https://www.googleapis.com/auth/drive');
        
        $this->client = $client;
        $this->service = new Google_Service_Sheets($client);
    }
    
    /**
     * Convert Google Drive link to direct image link
     */
    public function convertGoogleDriveLink($url)
    {
        if (empty($url) || !is_string($url)) {
            return $url;
        }
        
        $url = trim($url);
        
        if (!preg_match('/^https?:\/\//', $url)) {
            return $url;
        }
        
        if (strpos($url, 'drive.google.com') !== false) {
            $fileId = null;
            
            // Format 1: /file/d/FILE_ID/view or /edit
            if (preg_match('/\/file\/d\/([a-zA-Z0-9_-]+)/', $url, $matches)) {
                $fileId = $matches[1];
            }
            // Format 2: ?id=FILE_ID
            elseif (preg_match('/[?&]id=([a-zA-Z0-9_-]+)/', $url, $matches)) {
                $fileId = $matches[1];
            }
            
            if ($fileId) {
                return "https://drive.google.com/uc?export=view&id={$fileId}";
            }
        }
        
        return $url;
    }
    
    /**
     * Read products from sheets1
     */
    public function readProducts()
    {
        $now = time();
        if ($this->recordsCache && ($now - $this->cacheTimestamp) < $this->cacheTtl) {
            return $this->recordsCache;
        }
        
        try {
            $range = $this->productsSheetName . '!A:Z';
            $response = $this->service->spreadsheets_values->get($this->sheetId, $range);
            $values = $response->getValues();
            
            if (empty($values)) {
                return [];
            }
            
            $headers = array_shift($values);
            $products = [];
            
            foreach ($values as $row) {
                // Ensure row has same length as headers
                while (count($row) < count($headers)) {
                    $row[] = '';
                }
                
                $record = [];
                foreach ($headers as $idx => $header) {
                    $headerLower = strtolower(trim($header));
                    $record[$headerLower] = $row[$idx] ?? '';
                }
                
                $code = $record['code'] ?? '';
                $codeOriginal = $code;
                
                $normalized = [
                    'code' => $codeOriginal,
                    'code_normalized' => ProductUtils::normalizeCode($codeOriginal),
                    'quantity' => $record['quantity'] ?? $record['qty'] ?? $record['kv'] ?? '',
                    'collection' => $record['collection'] ?? '',
                    'date' => $record['date'] ?? $record['sana'] ?? '',
                ];
                
                $products[] = $normalized;
            }
            
            $this->recordsCache = $products;
            $this->cacheTimestamp = $now;
            
            return $products;
        } catch (\Exception $e) {
            error_log("Error reading products: " . $e->getMessage());
            return [];
        }
    }
    
    /**
     * Get image records from sheets2
     */
    private function getImageRecords()
    {
        $now = time();
        if ($this->imagesCache && ($now - $this->imagesCacheTimestamp) < $this->cacheTtl) {
            return $this->imagesCache;
        }
        
        try {
            $range = $this->imagesSheetName . '!A:Z';
            $response = $this->service->spreadsheets_values->get($this->sheetId, $range);
            $values = $response->getValues();
            
            if (empty($values) || count($values) < 2) {
                return [];
            }
            
            $headers = array_shift($values);
            $codeIdx = null;
            $imageIdx = null;
            
            foreach ($headers as $idx => $header) {
                $headerLower = strtolower(trim($header));
                if ($headerLower === 'code') {
                    $codeIdx = $idx;
                } elseif (in_array($headerLower, ['image_url', 'imageurl', 'image url', 'image'])) {
                    $imageIdx = $idx;
                }
            }
            
            if ($codeIdx === null) {
                return [];
            }
            
            $imageMap = [];
            foreach ($values as $row) {
                if ($codeIdx >= count($row)) {
                    continue;
                }
                
                $code = trim($row[$codeIdx] ?? '');
                if (empty($code)) {
                    continue;
                }
                
                $imageUrl = '';
                if ($imageIdx !== null && $imageIdx < count($row)) {
                    $imageUrl = trim($row[$imageIdx] ?? '');
                }
                
                if (!empty($imageUrl)) {
                    $codeNorm = ProductUtils::normalizeCode($code);
                    if ($codeNorm) {
                        $imageMap[$codeNorm] = $imageUrl;
                    }
                }
            }
            
            $this->imagesCache = $imageMap;
            $this->imagesCacheTimestamp = $now;
            
            return $imageMap;
        } catch (\Exception $e) {
            error_log("Error reading images: " . $e->getMessage());
            return [];
        }
    }
    
    /**
     * Get product data by code
     */
    public function getProductData($productCode)
    {
        $userCodeNorm = ProductUtils::normalizeCode($productCode);
        if (empty($userCodeNorm)) {
            return null;
        }
        
        try {
            $products = $this->readProducts();
            $matchedRows = [];
            
            foreach ($products as $row) {
                $rowCodeOriginal = $row['code'] ?? '';
                if (empty($rowCodeOriginal)) {
                    continue;
                }
                
                $sheetCodeNorm = $row['code_normalized'] ?? '';
                if (empty($sheetCodeNorm)) {
                    $sheetCodeNorm = ProductUtils::normalizeCode($rowCodeOriginal);
                }
                
                // Universal search: exact, startswith, endswith, or contains
                $matches = false;
                if ($sheetCodeNorm === $userCodeNorm) {
                    $matches = true;
                } elseif (strpos($sheetCodeNorm, $userCodeNorm) === 0) {
                    $matches = true;
                } elseif (substr($sheetCodeNorm, -strlen($userCodeNorm)) === $userCodeNorm) {
                    $matches = true;
                } elseif (strpos($sheetCodeNorm, $userCodeNorm) !== false) {
                    $matches = true;
                }
                
                if ($matches) {
                    $qty = $row['quantity'] ?? '';
                    $qtyStr = is_null($qty) ? '' : trim((string)$qty);
                    
                    $rowCopy = $row;
                    $rowCopy['quantity'] = $qtyStr;
                    $rowCopy['code_original'] = $rowCodeOriginal;
                    $matchedRows[] = $rowCopy;
                }
            }
            
            if (empty($matchedRows)) {
                return null;
            }
            
            // Get collection and date
            $collections = array_filter(array_column($matchedRows, 'collection'));
            $dates = array_filter(array_column($matchedRows, 'date'));
            
            $collection = count(array_unique($collections)) === 1 ? reset($collections) : 'N/A';
            $date = count(array_unique($dates)) === 1 ? reset($dates) : 'N/A';
            
            $originalCode = $matchedRows[0]['code_original'] ?? $matchedRows[0]['code'] ?? $productCode;
            
            // Add image URLs
            $imageMap = $this->getImageRecords();
            foreach ($matchedRows as &$item) {
                $code = $item['code_normalized'] ?? '';
                $item['image_url'] = $imageMap[$code] ?? '';
            }
            
            return [
                'code' => $userCodeNorm,
                'original_code' => $originalCode,
                'collection' => $collection,
                'date' => $date,
                'matched_rows' => $matchedRows,
            ];
        } catch (\Exception $e) {
            error_log("Error getting product data: " . $e->getMessage());
            return null;
        }
    }
    
    /**
     * Read prices from sheets3
     */
    public function readPricesFromSheets3()
    {
        try {
            $range = $this->pricesSheetName . '!A:Z';
            $response = $this->service->spreadsheets_values->get($this->sheetId, $range);
            $values = $response->getValues();
            
            if (empty($values) || count($values) < 2) {
                return [];
            }
            
            $headers = array_shift($values);
            $codeIdx = null;
            $modelNameIdx = null;
            $asosiyIdx = null;
            $miniIdx = null;
            $kasetniyIdx = null;
            $asosiyQimmatIdx = null;
            $miniQimmatIdx = null;
            $kasetniyQimmatIdx = null;
            $izohIdx = null;
            $collectionIdx = null;
            
            foreach ($headers as $idx => $header) {
                $headerNorm = strtolower(str_replace([' ', '_'], '', trim($header)));
                $headerOriginal = strtolower(trim($header));
                
                if (in_array($headerNorm, ['code', 'kod', 'код'])) {
                    $codeIdx = $idx;
                } elseif (strpos($headerNorm, 'modelnomi') !== false || 
                          strpos($headerNorm, 'modelname') !== false ||
                          (strpos($headerOriginal, 'madel') !== false && strpos($headerOriginal, 'nomi') !== false)) {
                    $modelNameIdx = $idx;
                } elseif (in_array($headerNorm, ['asosiy', 'основной'])) {
                    $asosiyIdx = $idx;
                } elseif (in_array($headerNorm, ['mini', 'мини'])) {
                    $miniIdx = $idx;
                } elseif (in_array($headerNorm, ['kasetniy', 'kasetni', 'кассетный', 'kaset'])) {
                    $kasetniyIdx = $idx;
                } elseif (strpos($headerNorm, 'asosiyqimmat') !== false) {
                    $asosiyQimmatIdx = $idx;
                } elseif (strpos($headerNorm, 'miniqimmat') !== false) {
                    $miniQimmatIdx = $idx;
                } elseif (strpos($headerNorm, 'kasetniyqimmat') !== false) {
                    $kasetniyQimmatIdx = $idx;
                } elseif (in_array($headerNorm, ['izoh', 'izox', 'примечание', 'comment'])) {
                    $izohIdx = $idx;
                } elseif (in_array($headerNorm, ['collection', 'kolleksiya', 'коллекция'])) {
                    $collectionIdx = $idx;
                }
            }
            
            if ($codeIdx === null) {
                return [];
            }
            
            $records = [];
            foreach (array_slice($values, 1) as $row) {
                while (count($row) < count($headers)) {
                    $row[] = '';
                }
                
                $code = trim($row[$codeIdx] ?? '');
                if (empty($code)) {
                    continue;
                }
                
                $records[] = [
                    'code' => $code,
                    'code_normalized' => ProductUtils::normalizeCode($code),
                    'collection' => trim($row[$collectionIdx] ?? ''),
                    'model_name' => trim($row[$modelNameIdx] ?? ''),
                    'asosiy_price' => trim($row[$asosiyIdx] ?? ''),
                    'mini_price' => trim($row[$miniIdx] ?? ''),
                    'kasetniy_price' => trim($row[$kasetniyIdx] ?? ''),
                    'izoh' => trim($row[$izohIdx] ?? ''),
                    'asosiy_qimmat' => trim($row[$asosiyQimmatIdx] ?? ''),
                    'mini_qimmat' => trim($row[$miniQimmatIdx] ?? ''),
                    'kasetniy_qimmat' => trim($row[$kasetniyQimmatIdx] ?? ''),
                ];
            }
            
            return $records;
        } catch (\Exception $e) {
            error_log("Error reading prices: " . $e->getMessage());
            return [];
        }
    }
    
    /**
     * Read discount prices from sheets4
     */
    public function readDiscountPricesFromSheets4()
    {
        try {
            $range = $this->discountSheetName . '!A:J';
            $response = $this->service->spreadsheets_values->get($this->sheetId, $range);
            $values = $response->getValues();
            
            if (empty($values) || count($values) < 2) {
                return [];
            }
            
            $records = [];
            foreach (array_slice($values, 1) as $row) {
                while (count($row) < 10) {
                    $row[] = '';
                }
                
                $code = trim($row[0] ?? '');
                if (empty($code)) {
                    continue;
                }
                
                $records[] = [
                    'code' => $code,
                    'code_normalized' => ProductUtils::normalizeCode($code),
                    'quantity' => trim($row[1] ?? ''),
                    'collection' => trim($row[2] ?? ''),
                    'date' => trim($row[3] ?? ''),
                    'model_name' => trim($row[4] ?? ''),
                    'old_price' => trim($row[5] ?? ''),
                    'price' => trim($row[6] ?? ''),
                    'mini_price' => trim($row[7] ?? ''),
                    'kasetniy_price' => trim($row[8] ?? ''),
                    'image_url' => trim($row[9] ?? ''),
                ];
            }
            
            return $records;
        } catch (\Exception $e) {
            error_log("Error reading discount prices: " . $e->getMessage());
            return [];
        }
    }
}

