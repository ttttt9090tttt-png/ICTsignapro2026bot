"""
ربات معاملاتی ICT با قابلیت انتخاب سبک معاملاتی
پشتیبانی از: اسکالپ، دی‌تریدینگ، سوئینگ، پوزیشن
نسخه 3.0
"""

import os
import time
import threading
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from flask import Flask
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import json
import warnings
warnings.filterwarnings('ignore')

# ============================================
# بخش ۱: تنظیمات اولیه
# ============================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8783309325:AAEH0EAc_cVzZkIvRratiOgTMzlVkai9e0w")
CHAT_ID = os.getenv("CHAT_ID", "1186512882")

# ============================================
# بخش ۱-الف: انتخاب سبک معاملاتی
# ============================================

TRADING_STYLE = os.getenv("TRADING_STYLE", "ALL")  
# گزینه‌ها: "SCALP", "DAY", "SWING", "POSITION", "ALL"

# ۱۵ ارز برتر کریپتو
TOP_CRYPTO = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "LTCUSDT", "NEARUSDT", "ATOMUSDT", "XLMUSDT"
]

# ============================================
# بخش ۱-ب: تنظیمات تایم‌فریم بر اساس سبک
# ============================================

TIMEFRAME_CONFIG = {
    "SCALP": {
        "timeframes": ["1m", "5m", "15m"],
        "lookback": 100,
        "check_interval": 60,
        "description": "اسکالپ - معاملات بسیار کوتاه‌مدت"
    },
    "DAY": {
        "timeframes": ["15m", "1h"],
        "lookback": 150,
        "check_interval": 180,
        "description": "دی‌تریدینگ - معاملات یک روزه"
    },
    "SWING": {
        "timeframes": ["1h", "4h", "daily"],
        "lookback": 200,
        "check_interval": 300,
        "description": "سوئینگ - معاملات چند روزه تا چند هفته"
    },
    "POSITION": {
        "timeframes": ["daily", "weekly"],
        "lookback": 300,
        "check_interval": 600,
        "description": "پوزیشن - معاملات بلندمدت"
    },
    "ALL": {
        "timeframes": ["15m", "1h", "4h", "daily"],
        "lookback": 150,
        "check_interval": 300,
        "description": "همه سبک‌ها"
    }
}

# دریافت تنظیمات بر اساس سبک انتخاب شده
style_config = TIMEFRAME_CONFIG.get(TRADING_STYLE, TIMEFRAME_CONFIG["ALL"])
TIMEFRAMES = style_config["timeframes"]
LOOKBACK_CANDLES = style_config["lookback"]
CHECK_INTERVAL = style_config["check_interval"]

print(f"""
╔═══════════════════════════════════════════════╗
║      🤖 ربات معاملاتی ICT - {TRADING_STYLE}       ║
║   سبک: {style_config['description']}           ║
║   تایم‌فریم‌ها: {', '.join(TIMEFRAMES)}        ║
║   فاصله اسکن: {CHECK_INTERVAL} ثانیه          ║
╚═══════════════════════════════════════════════╝
""")

app = Flask(__name__)
sent_signals = {}

# ============================================
# بخش ۲: ساختارهای داده
# ============================================

@dataclass
class OrderBlock:
    type: str
    high: float
    low: float
    open: float
    close: float
    timestamp: datetime
    strength: float

@dataclass
class FVG:
    type: str
    high: float
    low: float
    timestamp: datetime

@dataclass
class TradeSignal:
    type: str
    strength: str
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    confidence: float
    reasoning: List[str]
    setup_type: str
    style: str  # سبک معاملاتی
    timeframe: str

# ============================================
# بخش ۳: دریافت داده
# ============================================

def get_price_data(symbol, interval="1h", limit=150):
    try:
        # پشتیبانی از تایم‌فریم‌های مختلف
        interval_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1h", "4h": "4h", "daily": "1d",
            "weekly": "1w"
        }
        binance_interval = interval_map.get(interval, "1h")
        
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={binance_interval}&limit={limit}"
        response = requests.get(url, timeout=20)
        data = response.json()
        
        candles = []
        for candle in data:
            candles.append({
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
                "timestamp": datetime.fromtimestamp(candle[0] / 1000)
            })
        return candles
    except Exception as e:
        print(f"⚠️ خطا در دریافت {symbol}: {e}")
        return None

def df_from_candles(candles):
    if not candles:
        return None
    return pd.DataFrame(candles)

# ============================================
# بخش ۴: ارسال پیام
# ============================================

def send_msg(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=15)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ خطا در ارسال پیام: {e}")
        return False

# ============================================
# بخش ۵: تحلیلگر ICT
# ============================================

class ICTMarketAnalyzer:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        if df is not None and len(df) > 0:
            self.current_price = df['close'].iloc[-1]
        else:
            self.current_price = 0
    
    def identify_trend(self) -> Dict:
        if self.df is None or len(self.df) < 20:
            return {'direction': 'neutral', 'strength': 0}
        
        sma20 = self.df['close'].rolling(20).mean()
        sma50 = self.df['close'].rolling(50).mean()
        sma200 = self.df['close'].rolling(200).mean()
        
        current_price = self.current_price
        trend_score = 0
        
        if not sma20.empty and current_price > sma20.iloc[-1]:
            trend_score += 1
        if not sma50.empty and current_price > sma50.iloc[-1]:
            trend_score += 1
        if not sma200.empty and current_price > sma200.iloc[-1]:
            trend_score += 1
        
        highs = self.df['high'].values
        lows = self.df['low'].values
        
        if len(highs) > 10:
            recent_highs = highs[-10:]
            recent_lows = lows[-10:]
            hh_count = sum(1 for i in range(2, len(recent_highs)) 
                          if recent_highs[i] > recent_highs[i-1])
            hl_count = sum(1 for i in range(2, len(recent_lows)) 
                          if recent_lows[i] > recent_lows[i-1])
            
            if hh_count >= 3 and hl_count >= 3:
                direction = 'up'
                trend_score += 1
            elif hh_count <= -3 and hl_count <= -3:
                direction = 'down'
                trend_score -= 1
            else:
                direction = 'neutral'
        else:
            direction = 'neutral'
        
        if trend_score >= 3:
            direction = 'up'
            strength = min(trend_score * 20, 100)
        elif trend_score <= -3:
            direction = 'down'
            strength = min(abs(trend_score) * 20, 100)
        else:
            direction = 'neutral'
            strength = 0
        
        return {'direction': direction, 'strength': strength}
    
    def identify_range(self, period: int = 60) -> Dict:
        if self.df is None or len(self.df) < period:
            period = len(self.df)
        
        recent = self.df.tail(period)
        high = recent['high'].max()
        low = recent['low'].min()
        range_size = high - low
        
        premium_start = high - (range_size * 0.33)
        discount_end = low + (range_size * 0.33)
        
        current_price = self.current_price
        
        if current_price >= premium_start:
            zone = 'premium'
            zone_strength = (current_price - premium_start) / (high - premium_start) * 100
        elif current_price <= discount_end:
            zone = 'discount'
            zone_strength = (discount_end - current_price) / (discount_end - low) * 100
        else:
            zone = 'mid'
            zone_strength = 0
        
        return {
            'high': high, 'low': low,
            'premium_start': premium_start,
            'discount_end': discount_end,
            'current_price': current_price,
            'zone': zone,
            'zone_strength': zone_strength
        }
    
    def find_swing_points(self, lookback: int = 20) -> Tuple[List, List]:
        if self.df is None or len(self.df) < 5:
            return [], []
        
        highs = []
        lows = []
        
        for i in range(2, len(self.df) - 2):
            if (self.df['high'].iloc[i] > self.df['high'].iloc[i-1] and
                self.df['high'].iloc[i] > self.df['high'].iloc[i-2] and
                self.df['high'].iloc[i] > self.df['high'].iloc[i+1] and
                self.df['high'].iloc[i] > self.df['high'].iloc[i+2]):
                highs.append({'level': self.df['high'].iloc[i], 'index': i})
            
            if (self.df['low'].iloc[i] < self.df['low'].iloc[i-1] and
                self.df['low'].iloc[i] < self.df['low'].iloc[i-2] and
                self.df['low'].iloc[i] < self.df['low'].iloc[i+1] and
                self.df['low'].iloc[i] < self.df['low'].iloc[i+2]):
                lows.append({'level': self.df['low'].iloc[i], 'index': i})
        
        return highs, lows
    
    def find_order_blocks(self) -> List[OrderBlock]:
        if self.df is None or len(self.df) < 10:
            return []
        
        order_blocks = []
        avg_move = abs(self.df['close'].diff()).tail(10).mean()
        if avg_move == 0:
            avg_move = 1
        
        for i in range(3, len(self.df) - 3):
            strong_move = abs(self.df['close'].iloc[i] - self.df['close'].iloc[i-1]) > 2 * avg_move
            
            if strong_move:
                if self.df['close'].iloc[i] > self.df['close'].iloc[i-1]:
                    j = i - 1
                    while j > max(0, i - 5) and self.df['close'].iloc[j] < self.df['open'].iloc[j]:
                        j -= 1
                    if j < i - 1 and j >= 0:
                        order_blocks.append(OrderBlock(
                            type='buy', high=self.df['high'].iloc[j],
                            low=self.df['low'].iloc[j],
                            open=self.df['open'].iloc[j],
                            close=self.df['close'].iloc[j],
                            timestamp=self.df['timestamp'].iloc[j], strength=80
                        ))
                else:
                    j = i - 1
                    while j > max(0, i - 5) and self.df['close'].iloc[j] > self.df['open'].iloc[j]:
                        j -= 1
                    if j < i - 1 and j >= 0:
                        order_blocks.append(OrderBlock(
                            type='sell', high=self.df['high'].iloc[j],
                            low=self.df['low'].iloc[j],
                            open=self.df['open'].iloc[j],
                            close=self.df['close'].iloc[j],
                            timestamp=self.df['timestamp'].iloc[j], strength=80
                        ))
        
        return order_blocks
    
    def find_fvg(self) -> List[FVG]:
        if self.df is None or len(self.df) < 3:
            return []
        
        fvg_list = []
        for i in range(1, len(self.df) - 1):
            if self.df['high'].iloc[i+1] < self.df['low'].iloc[i-1]:
                fvg_list.append(FVG(
                    type='bullish',
                    high=self.df['low'].iloc[i-1],
                    low=self.df['high'].iloc[i+1],
                    timestamp=self.df['timestamp'].iloc[i]
                ))
            if self.df['low'].iloc[i+1] > self.df['high'].iloc[i-1]:
                fvg_list.append(FVG(
                    type='bearish',
                    high=self.df['low'].iloc[i+1],
                    low=self.df['high'].iloc[i-1],
                    timestamp=self.df['timestamp'].iloc[i]
                ))
        return fvg_list
    
    def find_liquidity_pools(self, periods: List[int] = [20, 40, 60]) -> Dict:
        if self.df is None or len(self.df) < 20:
            return {'buy_stops': [], 'sell_stops': []}
        
        liquidity = {'buy_stops': [], 'sell_stops': []}
        
        for period in periods:
            if len(self.df) >= period:
                recent = self.df.tail(period)
                for i in range(2, len(recent) - 2):
                    if (recent['high'].iloc[i] > recent['high'].iloc[i-1] and 
                        recent['high'].iloc[i] > recent['high'].iloc[i+1]):
                        liquidity['buy_stops'].append({'level': recent['high'].iloc[i], 'period': period})
                    if (recent['low'].iloc[i] < recent['low'].iloc[i-1] and 
                        recent['low'].iloc[i] < recent['low'].iloc[i+1]):
                        liquidity['sell_stops'].append({'level': recent['low'].iloc[i], 'period': period})
        
        return liquidity
    
    def calculate_ote(self, lookback: int = 20) -> Dict:
        if self.df is None or len(self.df) < lookback:
            return {'entry_zone_low': None, 'entry_zone_high': None}
        
        recent = self.df.tail(lookback)
        swing_high = recent['high'].max()
        swing_low = recent['low'].min()
        range_size = swing_high - swing_low
        
        ote_62 = swing_high - range_size * 0.62
        ote_786 = swing_high - range_size * 0.786
        
        return {
            'entry_zone_low': min(ote_62, ote_786),
            'entry_zone_high': max(ote_62, ote_786),
            'swing_high': swing_high,
            'swing_low': swing_low,
            'current_price': self.current_price
        }
    
    def find_bms(self) -> List[Dict]:
        if self.df is None or len(self.df) < 10:
            return []
        
        bms_points = []
        for i in range(5, len(self.df) - 1):
            if (self.df['high'].iloc[i] > self.df['high'].iloc[i-5:i].max() and
                self.df['close'].iloc[i] > self.df['close'].iloc[i-1]):
                bms_points.append({'type': 'bullish', 'level': self.df['high'].iloc[i]})
            if (self.df['low'].iloc[i] < self.df['low'].iloc[i-5:i].min() and
                self.df['close'].iloc[i] < self.df['close'].iloc[i-1]):
                bms_points.append({'type': 'bearish', 'level': self.df['low'].iloc[i]})
        
        return bms_points

# ============================================
# بخش ۶: تولید سیگنال بر اساس سبک
# ============================================

class ICTSignalGenerator:
    def __init__(self, analyzer: ICTMarketAnalyzer, style: str = "ALL"):
        self.analyzer = analyzer
        self.df = analyzer.df
        self.style = style
    
    def generate_signals(self, timeframe: str = "1h") -> List[TradeSignal]:
        signals = []
        
        if self.df is None or len(self.df) < 20:
            return signals
        
        trend = self.analyzer.identify_trend()
        zones = self.analyzer.identify_range()
        order_blocks = self.analyzer.find_order_blocks()
        fvg_list = self.analyzer.find_fvg()
        liquidity = self.analyzer.find_liquidity_pools()
        ote = self.analyzer.calculate_ote()
        bms = self.analyzer.find_bms()
        current_price = self.analyzer.current_price
        
        # ========== سیگنال خرید ==========
        buy_score = 0
        buy_reasoning = []
        
        if trend['direction'] == 'up':
            buy_score += 30
            buy_reasoning.append('✅ روند صعودی')
        elif trend['direction'] == 'neutral':
            buy_score += 10
            buy_reasoning.append('⚠️ روند خنثی')
        else:
            buy_reasoning.append('❌ روند نزولی')
        
        if zones['zone'] == 'discount':
            buy_score += 30
            buy_reasoning.append(f'✅ ناحیه Discount ({zones["zone_strength"]:.0f}%)')
        elif zones['zone'] == 'mid':
            buy_score += 10
            buy_reasoning.append('⚠️ ناحیه میانی')
        else:
            buy_reasoning.append('❌ ناحیه Premium')
        
        has_buy_ob = any(ob.type == 'buy' for ob in order_blocks)
        if has_buy_ob:
            buy_score += 20
            buy_reasoning.append('✅ Order Block صعودی')
        
        has_bullish_fvg = any(f.type == 'bullish' for f in fvg_list)
        if has_bullish_fvg:
            buy_score += 10
            buy_reasoning.append('✅ FVG صعودی')
        
        if ote['entry_zone_low'] and ote['entry_zone_high']:
            if ote['entry_zone_low'] <= current_price <= ote['entry_zone_high']:
                buy_score += 20
                buy_reasoning.append('✅ در ناحیه OTE')
        
        has_bullish_bms = any(b['type'] == 'bullish' for b in bms)
        if has_bullish_bms:
            buy_score += 10
            buy_reasoning.append('✅ BMS صعودی')
        
        # ========== سیگنال فروش ==========
        sell_score = 0
        sell_reasoning = []
        
        if trend['direction'] == 'down':
            sell_score += 30
            sell_reasoning.append('✅ روند نزولی')
        elif trend['direction'] == 'neutral':
            sell_score += 10
            sell_reasoning.append('⚠️ روند خنثی')
        else:
            sell_reasoning.append('❌ روند صعودی')
        
        if zones['zone'] == 'premium':
            sell_score += 30
            sell_reasoning.append(f'✅ ناحیه Premium ({zones["zone_strength"]:.0f}%)')
        elif zones['zone'] == 'mid':
            sell_score += 10
            sell_reasoning.append('⚠️ ناحیه میانی')
        else:
            sell_reasoning.append('❌ ناحیه Discount')
        
        has_sell_ob = any(ob.type == 'sell' for ob in order_blocks)
        if has_sell_ob:
            sell_score += 20
            sell_reasoning.append('✅ Order Block نزولی')
        
        has_bearish_fvg = any(f.type == 'bearish' for f in fvg_list)
        if has_bearish_fvg:
            sell_score += 10
            sell_reasoning.append('✅ FVG نزولی')
        
        if ote['entry_zone_low'] and ote['entry_zone_high']:
            if ote['entry_zone_low'] <= current_price <= ote['entry_zone_high']:
                sell_score += 20
                sell_reasoning.append('✅ در ناحیه OTE')
        
        has_bearish_bms = any(b['type'] == 'bearish' for b in bms)
        if has_bearish_bms:
            sell_score += 10
            sell_reasoning.append('✅ BMS نزولی')
        
        # ========== تولید سیگنال نهایی ==========
        # تنظیم آستانه بر اساس سبک
        threshold = {
            "SCALP": 50,
            "DAY": 55,
            "SWING": 60,
            "POSITION": 65,
            "ALL": 55
        }.get(self.style, 55)
        
        # سیگنال خرید
        if buy_score >= threshold:
            supports, resistances = self.analyzer.find_swing_points()
            stop_loss = current_price * 0.985
            tp1 = current_price * 1.015
            tp2 = current_price * 1.025
            
            if supports:
                stop_loss = min(s['level'] for s in supports) * 0.998
            if resistances:
                above = [r['level'] for r in resistances if r['level'] > current_price]
                if above:
                    tp1 = min(above) * 0.998
                    tp2 = max(above) * 0.998 if len(above) > 1 else tp1 * 1.01
            
            confidence = min(buy_score / 100, 100)
            strength = 'HIGH' if confidence >= 70 else 'MEDIUM' if confidence >= 50 else 'LOW'
            
            signals.append(TradeSignal(
                type='BUY', strength=strength, entry=round(current_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit_1=round(tp1, 4), take_profit_2=round(tp2, 4),
                confidence=round(confidence, 1), reasoning=buy_reasoning[:5],
                setup_type='ICT_BUY', style=self.style, timeframe=timeframe
            ))
        
        # سیگنال فروش
        if sell_score >= threshold:
            supports, resistances = self.analyzer.find_swing_points()
            stop_loss = current_price * 1.015
            tp1 = current_price * 0.985
            tp2 = current_price * 0.975
            
            if resistances:
                stop_loss = max(r['level'] for r in resistances) * 1.002
            if supports:
                below = [s['level'] for s in supports if s['level'] < current_price]
                if below:
                    tp1 = max(below) * 1.002
                    tp2 = min(below) * 1.002 if len(below) > 1 else tp1 * 0.99
            
            confidence = min(sell_score / 100, 100)
            strength = 'HIGH' if confidence >= 70 else 'MEDIUM' if confidence >= 50 else 'LOW'
            
            signals.append(TradeSignal(
                type='SELL', strength=strength, entry=round(current_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit_1=round(tp1, 4), take_profit_2=round(tp2, 4),
                confidence=round(confidence, 1), reasoning=sell_reasoning[:5],
                setup_type='ICT_SELL', style=self.style, timeframe=timeframe
            ))
        
        return signals

# ============================================
# بخش ۷: تولید پیام با توضیح سبک
# ============================================

def generate_ict_message(symbol: str, timeframe: str, signal: TradeSignal) -> str:
    """تولید پیام سیگنال ICT با توضیح سبک معاملاتی"""
    direction_icon = "🟢" if signal.type == 'BUY' else "🔴"
    direction_text = "خرید (Long)" if signal.type == 'BUY' else "فروش (Short)"
    
    style_names = {
        "SCALP": "⚡ اسکالپ (کوتاه‌مدت)",
        "DAY": "☀️ دی‌تریدینگ (یک روزه)",
        "SWING": "📈 سوئینگ (چند روزه)",
        "POSITION": "🏛️ پوزیشن (بلندمدت)",
        "ALL": "📊 ترکیبی"
    }
    
    strength_icons = {'HIGH': '🔥 قوی', 'MEDIUM': '📊 متوسط', 'LOW': '💡 ضعیف'}
    
    # محاسبه R:R
    risk = abs(signal.entry - signal.stop_loss)
    rr_1 = round(abs(signal.take_profit_1 - signal.entry) / risk, 2) if risk > 0 else 0
    rr_2 = round(abs(signal.take_profit_2 - signal.entry) / risk, 2) if risk > 0 else 0
    
    reasoning_text = "\n".join([f"• {r}" for r in signal.reasoning])
    
    msg = f"""
🏛️ **سیگنال ICT - {symbol}**

**جهت:** {direction_icon} {direction_text}
**سبک معاملاتی:** {style_names.get(signal.style, signal.style)}
**تایم‌فریم:** {timeframe}
**نوع ستاپ:** {signal.setup_type}
**قدرت سیگنال:** {strength_icons.get(signal.strength, signal.strength)}

📊 **نقاط کلیدی:**
💰 **قیمت ورود:** {signal.entry}
🛑 **حد ضرر (SL):** {signal.stop_loss}
🎯 **تارگت ۱ (TP1):** {signal.take_profit_1}
🎯 **تارگت ۲ (TP2):** {signal.take_profit_2}

⚖️ **نسبت ریسک به ریوارد (R:R):** {rr_1} / {rr_2}
📈 **اطمینان:** {signal.confidence}%

📋 **دلایل سیگنال:**
{reasoning_text}

⏰ **زمان:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⚠️ **مدیریت ریسک:** حداکثر ۲% سرمایه در هر معامله
"""
    return msg

# ============================================
# بخش ۸: اسکن و ارسال
# ============================================

def scan_and_send_signals():
    print(f"🔄 شروع اسکن ICT - سبک: {TRADING_STYLE} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    for symbol in TOP_CRYPTO:
        for timeframe in TIMEFRAMES:
            try:
                candles = get_price_data(symbol, timeframe, LOOKBACK_CANDLES)
                if not candles:
                    continue
                
                df = df_from_candles(candles)
                if df is None or len(df) < 20:
                    continue
                
                analyzer = ICTMarketAnalyzer(df)
                generator = ICTSignalGenerator(analyzer, TRADING_STYLE)
                signals = generator.generate_signals(timeframe)
                
                for signal in signals:
                    key = f"{symbol}_{timeframe}_{signal.type}_{signal.style}"
                    
                    if sent_signals.get(key) == signal.entry:
                        continue
                    
                    msg = generate_ict_message(symbol, timeframe, signal)
                    if send_msg(msg):
                        sent_signals[key] = signal.entry
                        print(f"✅ سیگنال ICT [{TRADING_STYLE}] ارسال شد: {symbol} - {timeframe} - {signal.type}")
                        time.sleep(1)
                        
            except Exception as e:
                print(f"❌ خطا در {symbol} {timeframe}: {e}")
                continue
    
    if len(sent_signals) > 200:
        sent_signals.clear()

# ============================================
# بخش ۹: وب‌سرور و اجرا
# ============================================

app = Flask(__name__)

@app.route('/')
def home():
    return f"""
    <h1>🏛️ ربات معاملاتی ICT</h1>
    <p><strong>سبک معاملاتی:</strong> {TRADING_STYLE}</p>
    <p><strong>توضیح:</strong> {style_config['description']}</p>
    <p><strong>تایم‌فریم‌ها:</strong> {', '.join(TIMEFRAMES)}</p>
    <p><strong>ارزهای تحت پوشش:</strong> {len(TOP_CRYPTO)} ارز</p>
    <p><strong>وضعیت:</strong> ✅ فعال</p>
    <p><strong>آخرین اسکن:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    """

@app.route('/ping')
def ping():
    return "🏓 Pong"

@app.route('/manual/<symbol>')
def manual_signal(symbol):
    if symbol not in TOP_CRYPTO:
        return f"❌ {symbol} در لیست ارزها وجود ندارد"
    
    candles = get_price_data(symbol, "1h", LOOKBACK_CANDLES)
    if not candles:
        return f"❌ خطا در دریافت داده {symbol}"
    
    df = df_from_candles(candles)
    analyzer = ICTMarketAnalyzer(df)
    generator = ICTSignalGenerator(analyzer, TRADING_STYLE)
    signals = generator.generate_signals("1h")
    
    if not signals:
        return f"ℹ️ هیچ سیگنالی برای {symbol} یافت نشد"
    
    for signal in signals[:2]:
        msg = generate_ict_message(symbol, "1h", signal)
        send_msg(msg)
    
    return f"✅ سیگنال‌های ICT برای {symbol} ارسال شد"

@app.route('/style/<style>')
def change_style(style):
    if style not in TIMEFRAME_CONFIG:
        return f"❌ سبک {style} وجود ندارد. گزینه‌ها: SCALP, DAY, SWING, POSITION, ALL"
    
    return f"""
    <h1>🔄 تغییر سبک معاملاتی</h1>
    <p>سبک جدید: <strong>{style}</strong></p>
    <p>توضیح: {TIMEFRAME_CONFIG[style]['description']}</p>
    <p>برای اعمال تغییر، متغیر محیطی TRADING_STYLE را به {style} تغییر دهید و Deploy مجدد کنید.</p>
    """

def run_scan_loop():
    print("🚀 ربات ICT شروع به کار کرد...")
    while True:
        try:
            scan_and_send_signals()
        except Exception as e:
            print(f"❌ خطا در حلقه اصلی: {e}")
        time.sleep(CHECK_INTERVAL)

# ============================================
# بخش ۱۰: اجرای اصلی
# ============================================

if __name__ == "__main__":
    worker = threading.Thread(target=run_scan_loop, daemon=True)
    worker.start()
    
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 وب‌سرور روی پورت {port} راه‌اندازی شد...")
    app.run(host="0.0.0.0", port=port)
