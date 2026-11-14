//+------------------------------------------------------------------+
//|                                          TSE_SignalExecutor.mq5  |
//|  Minimal EA: reads TSE signal drop-file and opens Design A/B     |
//|  trades inside Hudsonventura MT5 (no REST bridge required).      |
//|                                                                  |
//|  Drop file (MQL5/Files/tse_signals.csv):                         |
//|  signal_id,instrument,direction,design,entry,sl,tp,horizon_bars  |
//|                                                                  |
//|  Design A: SL only, time-exit after horizon_bars × period        |
//|  Design B: SL + TP from research net                             |
//|                                                                  |
//|  Also exports broker M30 candles for the scanner:                |
//|  MQL5/Files/tse_candles_<PAIR>.csv  (time_utc,open,high,low,     |
//|  close,tick_volume; completed bars only; UTC epoch seconds) and  |
//|  tse_candles_ready.txt once every symbol has been written.       |
//+------------------------------------------------------------------+
#property copyright "TSE Signal Interface"
#property version   "1.00"
#property strict

// No #include <Trade/Trade.mqh>: the Hudsonventura compiler container ships no stock MQL5
// library, so orders are sent with the built-in OrderSend() and MqlTradeRequest below.

//---------------------- inputs ---------------------------------------
input string InpSignalFile     = "tse_signals.csv";   // file in MQL5/Files/
input string InpProcessedFile  = "tse_processed.txt"; // already-handled IDs
input int    InpMagicBase      = 51000;               // magic = base + design offset
input double InpLots           = 0.01;                // fixed lots (demo-safe)
input int    InpMaxOpen        = 4;                   // max simultaneous TSE positions
input int    InpPollSeconds    = 15;                  // how often to poll the file
input int    InpSlippagePoints = 30;
input bool   InpDryRun         = true;                // true = log only, no OrderSend
input bool   InpAllowDesignA   = true;
input bool   InpAllowDesignB   = true;
input string InpExportSymbols  = "";                  // e.g. XAUUSD,EURUSD,GBPUSD (empty = no candle export)
input string InpSymbolSuffix   = "";                  // broker suffix, e.g. ".m" (applies to signals and export)
input int    InpCandleBars     = 400;                 // completed M30 bars to export per symbol
input int    InpCandleMinBars  = 300;                 // below this the symbol is not "ready" yet

//---------------------- state ----------------------------------------
string processed_ids[];   // signal_ids already acted on this session + file
bool   candles_done = false;

//+------------------------------------------------------------------+
int OnInit()
{
   LoadProcessedIds();
   EventSetTimer(InpPollSeconds);

   PrintFormat("TSE_SignalExecutor init | file=%s dry_run=%s poll=%ds",
               InpSignalFile, (InpDryRun ? "YES" : "NO"), InpPollSeconds);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   SaveProcessedIds();
}

void OnTimer()
{
   if(!candles_done && StringLen(InpExportSymbols) > 0)
      ExportAllCandles();
   ProcessSignalFile();
   ManageOpenTrades();
}

void OnTick()
{
   // optional: tighter management can go here; timer is enough for M30 horizon
}

//+------------------------------------------------------------------+
//| Signal file processing                                           |
//+------------------------------------------------------------------+
void ProcessSignalFile()
{
   int h = FileOpen(InpSignalFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
   {
      // file not present yet – normal when no new signals
      return;
   }

   int opened = 0;
   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) == 0) continue;
      if(StringFind(line, "signal_id") == 0) continue; // header

      string parts[];
      int n = StringSplit(line, ',', parts);
      if(n < 8)
      {
         Print("TSE: skip malformed line: ", line);
         continue;
      }

      string signal_id  = parts[0];
      string instrument = parts[1];
      string direction  = parts[2];   // BUY / SELL
      string design     = parts[3];   // A / B
      double entry_ref  = StringToDouble(parts[4]);
      double sl_ref     = StringToDouble(parts[5]);
      double tp_ref     = StringToDouble(parts[6]);   // 0 or empty for Design A
      int    horizon    = (int)StringToInteger(parts[7]);

      if(IsProcessed(signal_id))
         continue;

      if(design == "A" && !InpAllowDesignA) { MarkProcessed(signal_id); continue; }
      if(design == "B" && !InpAllowDesignB) { MarkProcessed(signal_id); continue; }

      // Only trade the chart symbol by default (attach EA to each symbol, or expand later)
      if(instrument + InpSymbolSuffix != _Symbol)
      {
         // still mark processed so we don't spam logs every poll
         // Comment out MarkProcessed if you run one EA multi-symbol via SymbolSelect
         PrintFormat("TSE: signal %s for %s ignored on chart %s", signal_id, instrument, _Symbol);
         MarkProcessed(signal_id);
         continue;
      }

      if(CountOurPositions() >= InpMaxOpen)
      {
         Print("TSE: max open reached, deferring ", signal_id);
         break;
      }

      bool ok = OpenFromSignal(signal_id, direction, design, sl_ref, tp_ref, horizon);
      MarkProcessed(signal_id);
      if(ok) opened++;
   }
   FileClose(h);

   if(opened > 0)
      SaveProcessedIds();
}

//+------------------------------------------------------------------+
bool OpenFromSignal(const string signal_id, const string direction,
                    const string design, const double sl_ref,
                    const double tp_ref, const int horizon)
{
   long magic = InpMagicBase + (design == "B" ? 2 : 1);

   double price = (direction == "BUY") ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                       : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double sl = NormalizeDouble(sl_ref, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   double tp = 0.0;
   if(design == "B" && tp_ref > 0.0)
      tp = NormalizeDouble(tp_ref, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));

   // MT5 truncates order comments (~31 chars): keep the horizon BEFORE the long signal id
   // so ManageOpenTrades() can still read "|H<bars>" for the Design A time-exit.
   string comment = StringFormat("TSE|%s|H%d|%s", design, horizon, signal_id);

   PrintFormat("TSE: %s %s %s lots=%.2f sl=%.5f tp=%.5f | %s",
               (InpDryRun ? "DRY" : "LIVE"), direction, _Symbol, InpLots, sl, tp, comment);

   if(InpDryRun)
      return true;

   uint retcode = 0;
   bool result = SendMarketOrder((direction == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,
                                 InpLots, price, sl, tp, comment, magic, retcode);
   if(!result)
      Print("TSE: Order failed retcode=", retcode);
   return result;
}

//+------------------------------------------------------------------+
//| Design A time-exit + basic hygiene                               |
//+------------------------------------------------------------------+
void ManageOpenTrades()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      long magic = PositionGetInteger(POSITION_MAGIC);
      if(magic != InpMagicBase + 1 && magic != InpMagicBase + 2) continue;

      string comment = PositionGetString(POSITION_COMMENT);
      // comment format: TSE|A|H48|signal_id
      int hpos = StringFind(comment, "|H");
      if(hpos < 0) continue;

      int horizon = (int)StringToInteger(StringSubstr(comment, hpos + 2));
      if(horizon <= 0) horizon = 48;

      // Design A only (magic base+1): close after horizon × period seconds
      if(magic == InpMagicBase + 1)
      {
         datetime open_time = (datetime)PositionGetInteger(POSITION_TIME);
         int period_sec = PeriodSeconds(PERIOD_M30); // research contract is M30
         int max_life = horizon * period_sec;
         if(TimeCurrent() - open_time >= max_life)
         {
            PrintFormat("TSE: Design A time-exit ticket=%I64u after %d bars", ticket, horizon);
            if(!InpDryRun)
            {
               if(!ClosePositionByTicket(ticket))
                  Print("TSE: time-exit close failed for ticket ", ticket);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Broker candle export (read by scan_once.py through KB_CANDLE_DIR)|
//+------------------------------------------------------------------+
int ServerUtcOffsetSeconds()
{
   double d = (double)(TimeTradeServer() - TimeGMT());
   return (int)(MathRound(d / 1800.0) * 1800.0);   // whole half-hours (DST-aware)
}

bool ExportCandles(const string base, const int offset)
{
   string sym = base + InpSymbolSuffix;
   if(!SymbolSelect(sym, true))
   {
      PrintFormat("TSE: candles: SymbolSelect failed for %s", sym);
      return false;
   }
   MqlRates rates[];
   ArraySetAsSeries(rates, false);                       // index 0 = oldest
   int got = CopyRates(sym, PERIOD_M30, 1, InpCandleBars, rates);   // start 1 = skip the forming bar
   if(got < InpCandleMinBars)
   {
      PrintFormat("TSE: candles: %s has %d M30 bars (need %d), retrying", sym, got, InpCandleMinBars);
      return false;
   }
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   string tmp = "tse_candles_" + base + ".tmp";
   string dst = "tse_candles_" + base + ".csv";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      PrintFormat("TSE: candles: cannot write %s (error %d)", tmp, GetLastError());
      return false;
   }
   FileWriteString(h, "time_utc,open,high,low,close,tick_volume\n");
   for(int i = 0; i < got; i++)
   {
      FileWriteString(h, StringFormat("%I64d,%s,%s,%s,%s,%I64d\n",
                      (long)rates[i].time - (long)offset,
                      DoubleToString(rates[i].open,  digits),
                      DoubleToString(rates[i].high,  digits),
                      DoubleToString(rates[i].low,   digits),
                      DoubleToString(rates[i].close, digits),
                      (long)rates[i].tick_volume));
   }
   FileClose(h);
   if(!FileMove(tmp, 0, dst, FILE_REWRITE))
   {
      PrintFormat("TSE: candles: FileMove to %s failed (error %d)", dst, GetLastError());
      return false;
   }
   PrintFormat("TSE: candles: %s exported %d bars, newest open (server) %s", sym, got,
               TimeToString(rates[got - 1].time, TIME_DATE | TIME_MINUTES));
   return true;
}

void ExportAllCandles()
{
   string syms[];
   int n = StringSplit(InpExportSymbols, ',', syms);
   int offset = ServerUtcOffsetSeconds();
   int want = 0, ok = 0;
   for(int i = 0; i < n; i++)
   {
      string b = syms[i];
      StringTrimLeft(b);
      StringTrimRight(b);
      if(StringLen(b) == 0) continue;
      want++;
      if(ExportCandles(b, offset)) ok++;
   }
   if(want > 0 && ok == want)
   {
      int h = FileOpen("tse_candles_ready.txt", FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(h != INVALID_HANDLE)
      {
         FileWriteString(h, StringFormat("OK n=%d server_utc_offset=%d\n", ok, offset));
         FileClose(h);
         candles_done = true;
         PrintFormat("TSE: candles: all %d symbols exported (server UTC offset %d s)", ok, offset);
      }
   }
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING FillingFor(const string sym)
{
   int fm = (int)SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
   if((fm & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK) return ORDER_FILLING_FOK;
   if((fm & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

bool DealAccepted(const uint rc)
{
   return (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL || rc == TRADE_RETCODE_PLACED);
}

bool SendMarketOrder(const ENUM_ORDER_TYPE type, const double lots, const double price,
                     const double sl, const double tp, const string cmt,
                     const long magic, uint &retcode)
{
   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req);
   ZeroMemory(res);
   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = _Symbol;
   req.volume       = lots;
   req.type         = type;
   req.price        = price;
   req.sl           = sl;
   req.tp           = tp;
   req.deviation    = InpSlippagePoints;
   req.magic        = magic;
   req.comment      = cmt;
   req.type_filling = FillingFor(_Symbol);
   bool sent = OrderSend(req, res);
   retcode = res.retcode;
   return (sent && DealAccepted(res.retcode));
}

bool ClosePositionByTicket(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket)) return false;
   string sym   = PositionGetString(POSITION_SYMBOL);
   long   ptype = PositionGetInteger(POSITION_TYPE);

   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req);
   ZeroMemory(res);
   req.action       = TRADE_ACTION_DEAL;
   req.position     = ticket;
   req.symbol       = sym;
   req.volume       = PositionGetDouble(POSITION_VOLUME);
   req.type         = (ptype == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   req.price        = (ptype == POSITION_TYPE_BUY) ? SymbolInfoDouble(sym, SYMBOL_BID)
                                                    : SymbolInfoDouble(sym, SYMBOL_ASK);
   req.deviation    = InpSlippagePoints;
   req.magic        = PositionGetInteger(POSITION_MAGIC);
   req.type_filling = FillingFor(sym);
   bool sent = OrderSend(req, res);
   return (sent && DealAccepted(res.retcode));
}

int CountOurPositions()
{
   int n = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(magic == InpMagicBase + 1 || magic == InpMagicBase + 2)
         n++;
   }
   return n;
}

bool IsProcessed(const string id)
{
   for(int i = 0; i < ArraySize(processed_ids); i++)
      if(processed_ids[i] == id) return true;
   return false;
}

void MarkProcessed(const string id)
{
   if(IsProcessed(id)) return;
   int n = ArraySize(processed_ids);
   ArrayResize(processed_ids, n + 1);
   processed_ids[n] = id;
}

void LoadProcessedIds()
{
   ArrayResize(processed_ids, 0);
   int h = FileOpen(InpProcessedFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE) return;
   while(!FileIsEnding(h))
   {
      string id = FileReadString(h);
      StringTrimLeft(id);
      StringTrimRight(id);
      if(StringLen(id) > 0) MarkProcessed(id);
   }
   FileClose(h);
}

void SaveProcessedIds()
{
   int h = FileOpen(InpProcessedFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   for(int i = 0; i < ArraySize(processed_ids); i++)
      FileWriteString(h, processed_ids[i] + "\n");
   FileClose(h);
}
//+------------------------------------------------------------------+
