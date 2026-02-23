<?php
/**
 * OpenAI Service
 */

namespace MyBot\Services;

use GuzzleHttp\Client;

class AIService
{
    private $apiKey;
    private $model;
    private $client;
    
    private const AI_GENERATION_USER_PROMPT = "Foydalanuvchi yuborgan 1-rasm — xona derazasi.
2-rasm — jalyuzi modeli.

Jalyuzi modelini derazaga O'RNATILGAN HOLATDA tasavvur qilib,
rang uyg'unligi, uslub, proporsiya va amaliy jihatdan tahlil qil.

Faqat vizual natijani va dizayn tavsiyasini yoz.";
    
    public function __construct()
    {
        $this->apiKey = OPENAI_API_KEY;
        $this->model = OPENAI_MODEL;
        
        if (empty($this->apiKey)) {
            throw new \Exception("OPENAI_API_KEY is required");
        }
        
        $this->client = new Client([
            'base_uri' => 'https://api.openai.com/v1/',
            'headers' => [
                'Authorization' => 'Bearer ' . $this->apiKey,
                'Content-Type' => 'application/json',
            ],
        ]);
    }
    
    /**
     * Generate from two images
     */
    public function generateFromImages($roomBytes, $modelBytes)
    {
        $roomB64 = base64_encode($roomBytes);
        $modelB64 = base64_encode($modelBytes);
        
        $messages = [
            [
                'role' => 'user',
                'content' => [
                    [
                        'type' => 'text',
                        'text' => self::AI_GENERATION_USER_PROMPT
                    ],
                    [
                        'type' => 'image_url',
                        'image_url' => [
                            'url' => 'data:image/png;base64,' . $roomB64
                        ]
                    ],
                    [
                        'type' => 'image_url',
                        'image_url' => [
                            'url' => 'data:image/png;base64,' . $modelB64
                        ]
                    ]
                ]
            ]
        ];
        
        try {
            $response = $this->client->post('chat/completions', [
                'json' => [
                    'model' => $this->model,
                    'messages' => $messages,
                    'temperature' => 0.7,
                    'max_tokens' => 1000,
                ]
            ]);
            
            $result = json_decode($response->getBody()->getContents(), true);
            
            $description = $result['choices'][0]['message']['content'] ?? '✅ Natija tayyor.';
            
            return [
                'image_bytes' => null, // OpenAI Chat API doesn't return images
                'description' => $description,
            ];
        } catch (\Exception $e) {
            error_log("OpenAI generation failed: " . $e->getMessage());
            throw $e;
        }
    }
    
    /**
     * Generate answer for general questions
     */
    public function generateAnswer($text = null, $imageBytes = null)
    {
        if (empty($text) && empty($imageBytes)) {
            throw new \Exception("Matn yoki rasm bo'lishi shart.");
        }
        
        $messages = [];
        if ($text) {
            $messages[] = [
                'role' => 'user',
                'content' => $text
            ];
        } elseif ($imageBytes) {
            $imageB64 = base64_encode($imageBytes);
            $messages[] = [
                'role' => 'user',
                'content' => [
                    [
                        'type' => 'text',
                        'text' => 'Rasm tahlil qilish'
                    ],
                    [
                        'type' => 'image_url',
                        'image_url' => [
                            'url' => 'data:image/png;base64,' . $imageB64
                        ]
                    ]
                ]
            ];
        }
        
        try {
            $response = $this->client->post('chat/completions', [
                'json' => [
                    'model' => $this->model,
                    'messages' => $messages,
                    'temperature' => 0.4,
                ]
            ]);
            
            $result = json_decode($response->getBody()->getContents(), true);
            return $result['choices'][0]['message']['content'] ?? 'Javob topilmadi.';
        } catch (\Exception $e) {
            error_log("OpenAI QA failed: " . $e->getMessage());
            throw $e;
        }
    }
}

