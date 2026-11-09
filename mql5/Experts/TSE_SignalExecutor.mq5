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
//+------------------------------------------------------------------+
#property copyright "TSE Signal Interface"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

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

//---------------------- state ----------------------------------------
CTrade trade;
string processed_ids[];   // signal_ids already acted on this session + file

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagicBase);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

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
      if(instrument != _Symbol)
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
   int magic = InpMagicBase + (design == "B" ? 2 : 1);
   trade.SetExpertMagicNumber(magic);

   double price = (direction == "BUY") ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                       : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double sl = NormalizeDouble(sl_ref, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   double tp = 0.0;
   if(design == "B" && tp_ref > 0.0)
      tp = NormalizeDouble(tp_ref, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));

   string comment = StringFormat("TSE|%s|%s|H%d", design, signal_id, horizon);

   PrintFormat("TSE: %s %s %s lots=%.2f sl=%.5f tp=%.5f | %s",
               (InpDryRun ? "DRY" : "LIVE"), direction, _Symbol, InpLots, sl, tp, comment);

   if(InpDryRun)
      return true;

   bool result = false;
   if(direction == "BUY")
      result = trade.Buy(InpLots, _Symbol, price, sl, tp, comment);
   else
      result = trade.Sell(InpLots, _Symbol, price, sl, tp, comment);

   if(!result)
      Print("TSE: Order failed retcode=", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
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
      // comment format: TSE|A|signal_id|H48
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
            PrintFormat("TSE: Design A time-exit ticket=%d after %d bars", ticket, horizon);
            if(!InpDryRun)
               trade.PositionClose(ticket);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
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
