#!/usr/local/bin/python3

import argparse
import random
import pandas as pd
import numpy as np
import datetime as dt
import time
from pytz import timezone
import logging

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.ticktype import TickTypeEnum
from ibapi.order import Order

pd.set_option('display.max_colwidth', 10)
pd.set_option('display.float_format', lambda x: '%.f' % x)

class IBConnectionError(Exception):
    pass

def codes(code):
    # https://interactivebrokers.github.io/tws-api/message_codes.html
    if len(str(code)) == 4 and str(code).startswith('1'):
        return 'SYSTEM'
    elif len(str(code)) == 4 and str(code).startswith('21'):
        return 'WARNING'
    elif len(str(code)) == 3 and str(code).startswith('5'):
        return 'CLIENT ERROR'
    elif len(str(code)) == 3 and int(str(code)[0]) in {1,2,3,4,5} or len(str(code)) == 5:
        return 'TWS ERROR'
    else:
        raise ValueError

class MarketDataApp(EClient, EWrapper):
    RT_BAR_PERIOD = 5
    def __init__(self, args):
        EClient.__init__(self, self)
        self.args = args
        self.RT_BAR_PERIOD = MarketDataApp.RT_BAR_PERIOD
        self.period = args.bar_period
        self.order_type = args.order_type
        self.order_size = args.order_size
        df_cols = {
            'time': [],
            'open': [],
            'high': [],
            'low': [],
            'close': [],
            'ha_open': [],
            'ha_close': [],
            'ha_high': [],
            'ha_low': [],
            'ha_color': [],
        }
        self.candles = pd.DataFrame(df_cols)
        self.cache = []
        self._tohlc = tuple() # Real-time 5s update data from IB
        self.first_order = True # Set to False after first order
        #
        self.connect("127.0.0.1", args.port, random.randint(0, 999))
        while not self.isConnected():
            print('Connecting to IB..')
            time.sleep(0.5)
        self.reqGlobalCancel()
        self.reqAllOpenOrders() ### tmp

        self.best_bid = None
        self.best_ask = None
        self.contract = self._create_contract_obj()
        ###
        self.mktData_reqId = random.randint(0, 999)
        while True:
            self.rtBars_reqId = random.randint(0, 999)
            if not self.rtBars_reqId == self.mktData_reqId:
                break
        self._subscribe_mktData()
        self._subscribe_rtBars()

    def error(self, reqId, errorCode, errorString):
        print(f'{codes(errorCode)}, {errorCode}, {errorString}')

    def _subscribe_mktData(self):
        self.reqMktData(self.mktData_reqId, self.contract, '', False, False, [])

    def _subscribe_rtBars(self):
        self.reqRealTimeBars(
            self.rtBars_reqId,
            self.contract,
            self.RT_BAR_PERIOD,
            "MIDPOINT", False, [])

    def tickPrice(self, reqId, tickType, price, attrib):
	    if tickType == 1 and reqId == self.mktData_reqId:
                # Bid
                self.best_bid = price
                #print('Bid update:', price)
	    if tickType == 2 and reqId == self.mktData_reqId:
                # Ask
                self.best_ask = price
                #print('Ask update:', price)

    def nextValidId(self, orderId: int):
            super().nextValidId(orderId)
            self.nextorderId = orderId
            print('The next valid order id is: ', self.nextorderId)

    def orderStatus(
	    self, orderId, status, filled, remaining, avgFullPrice,
	    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        print(
            'orderStatus - orderid:', orderId, 'status:', status,
            'filled', filled, 'remaining', remaining,
            'lastFillPrice', lastFillPrice)

    def openOrder(self, orderId, contract, order, orderState):
        print(
            'openOrder id:', orderId, contract.symbol, contract.secType,
            '@', contract.exchange, ':', order.action, order.orderType,
            order.totalQuantity, orderState.status)

    def execDetails(self, reqId, contract, execution):
        print(
            'Order Executed: ', reqId, contract.symbol,
            contract.secType, contract.currency, execution.execId,
            execution.orderId, execution.shares, execution.lastLiquidity)

    def historicalData(self, reqId, bar):
        print(
            "HistoricalData: ", reqId, "Date:", bar.date,
            "Open:", bar.open, "High:", bar.high,
            "Low:", bar.low, "Close:", bar.close)

    def realtimeBar(self, reqId, time, open_, high, low, close, volume, wap, count):
        super().realtimeBar(reqId, time, open_, high, low, close, volume, wap, count)
        self._tohlc = (time, open_, high, low, close)
        print('--')
        print(
            "RealTimeBar. TickerId:", reqId,
            dt.datetime.fromtimestamp(time), -1,
            self._tohlc[1:], volume, wap, count)
        #
        self._on_update()

    def _on_update(self):
        # Process 5s updates as received
        self._cache_update(self._tohlc)
        if self._check_period():
            self._update_candles()
            self.cache = []
            if self.candles.shape[0] > 0:
                #
                self._check_order_conditions()

    def _check_period(self):
        # Return True if period ends at this update, else False
        _time = dt.datetime.fromtimestamp(self._tohlc[0])
        total_secs = _time.hour*60 + _time.minute*60 + _time.second
        if total_secs % self.period == 0:
            return True
        return False

    def _update_candles(self):
        # Bar completed
        if self.cache[-1][0] - self.cache[0][0] + self.RT_BAR_PERIOD == self.period:
            _pd = self._calc_new_candle()
            self.candles = self.candles.append(_pd, ignore_index=True)
            #
            bar_color = None
            bar_color_prev = None
            if self.candles.shape[0] > 1:
                bar_color = self.candles['ha_color'].values[-1].upper()
            else:
                # First HA candle not yet available
                return
            if self.candles.shape[0] > 2:
                bar_color_prev = self.candles['ha_color'].values[-2].upper()
            print('--')
            print(f"New candle added: {bar_color}. Prev: {bar_color_prev} ")
        elif self.cache[-1][0] - self.cache[0][0] + self.RT_BAR_PERIOD < self.period:
            # First iteration. Not enough updates for a full period
            print('Not enough data for a candle')
        else:
            raise ValueError

    def _calc_new_candle(self):
        ohlc = (
            self.cache[0][1],
            max([u[2] for u in self.cache]),
            min([u[3] for u in self.cache]),
            self.cache[-1][4]
        )
        if self.candles.shape[0] > 0:
            # Can only calc heikin-ashi if we have previous data
            ha_ochl = (None, None, None, None) ### Placeholder
            ha_c = (ohlc[0] + ohlc[1] + ohlc[2] + ohlc[3])/4
            ha_o = (self.candles['ha_open'].values[-1] + self.candles['ha_close'].values[-1])/2
            ha_h = max(ohlc[1], ha_o, ha_c)
            ha_l = min(ohlc[2], ha_o, ha_c)
            ha_color = 'Red' if self.candles['ha_close'].values[-1] > ha_c else 'Green'
            ha_ochl = (ha_o, ha_c, ha_h, ha_l, ha_color)
        else:
            ha_ochl = (None, None, None, None, None)
        _pd = {
            'time': self._tohlc[0] ,
            'open': ohlc[0],
            'high': ohlc[1],
            'low': ohlc[2],
            'close': ohlc[3],
            'ha_open': ha_ochl[0],
            'ha_close': ha_ochl[1],
            'ha_high': ha_ochl[2],
            'ha_low': ha_ochl[3],
            'ha_color': ha_ochl[4],
        }
        return _pd

    def _cache_update(self, ohlc):
        # Still in the middle of a period. Cache data for processing at end of period
        self.cache.append(ohlc)

    def _check_order_conditions(self):
        if not isinstance(self.candles['ha_color'].values[-1], str):
            # Skip if first HA candle not yet available
            return
        #
        _side = 'Buy'
        if self.candles['ha_color'].values[-1] == 'Red':
            _side = 'Sell'
        #
        if self.first_order:
            self._place_order(_side)
            self.first_order = False
            self.order_size *= 2
        elif not self.candles['ha_color'].values[-1] == self.candles['ha_color'].values[-2]:
            self._place_order(_side)
        else:
            pass

    def _place_order(self, side):
        order = self._create_order_obj(side)
        print('Order -- ', side, self.order_type, self.order_size)
        self.placeOrder(self.nextorderId, self.contract, self._create_order_obj(side))
        self.nextorderId += 1
        #self.reqIds(0)

    def _check_ORH(self):
        # return True if outside regular hours, else False
        now = dt.datetime.now(timezone('US/Eastern'))
        if now.hour*60 + now.minute < 570:
            # Pre-market
            return True
        elif now.hour >= 16:
            # After-market
            return True
        else:
            # Regular trading hours
            return False

    def _create_order_obj(self, side):
        order = Order()
        order.action = side.upper()
        order.totalQuantity = self.order_size
        if self._check_ORH():
            order.orderType = self.order_type = 'LMT'
        else:
            order.orderType = self.order_type = self.args.order_type
        price = 0
        if self.order_type == 'LMT':
            order.sweepToFill = True
            if side == 'Buy':
                price = self.best_bid
            elif side == 'Sell':
                price = self.best_ask
        order.lmtPrice = price
        order.outsideRth = True
        return order

    def _create_contract_obj(self):
        contract = Contract()
        contract.symbol = self.args.symbol
        contract.secType = self.args.security_type
        contract.exchange = self.args.exchange
        contract.currency = self.args.currency
        return contract

def main():
    args = parse_args()
    app = MarketDataApp(args)
    app.run()

def parse_args():
    argp = argparse.ArgumentParser()
    argp.add_argument("symbol", type=str, default=None)
    argp.add_argument(
        "-d", "--debug", action="store_true", help="turn on debug logging"
    )
    argp.add_argument(
        "-p", "--port", type=int, default=4002, help="local port for connection: 7496/7497 for TWS prod/paper, 4001/4002 for Gateway prod/paper"
    )
    argp.add_argument(
        "-c", "--currency", type=str, default="USD", help="currency for symbols"
    )
    argp.add_argument(
        "-e", "--exchange", type=str, default="SMART", help="exchange for symbols"
    )
    argp.add_argument(
        "-t", "--security-type", type=str, default="STK", help="security type for symbols"
    )
    argp.add_argument(
        "-b", "--bar-period", type=int, default=60, help="bar time period"
    )
    argp.add_argument(
        "-s", "--order-size", type=int, default=100, help="Order size"
    )
    argp.add_argument(
        "-o", "--order-type", type=str, default='MKT', help="Order type"
    )

    args = argp.parse_args()
    return args

if __name__ == "__main__":
    main()
