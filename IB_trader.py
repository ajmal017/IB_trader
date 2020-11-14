#!/usr/local/bin/python3

import argparse
import random
import pandas as pd
import numpy as np
import datetime as dt
import os
import csv
import time
from pytz import timezone
import logging
import copy
import concurrent.futures

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.ticktype import TickTypeEnum
from ibapi.order import Order

pd.set_option('display.max_colwidth', 10)
pd.set_option('display.float_format', lambda x: '%.f' % x)


class IBConnectionError(Exception):
    pass


class ApplicationLogicError(Exception):
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
    """
        IB client for interacting with Gateway and TWS

        Arguments
        ---------
        client_id (int):      unique client ID per instrument traded
        args (obj):           runtime args, passed in from user (cli/gui)
        start_order_id (int): order IDs are incremented starting from this
            If start_order_id == None, then use self.reqIds() to increment order Ids
    """

    RT_BAR_PERIOD = 5
    def __init__(self, client_id, args, start_order_id=None):
        EClient.__init__(self, self)
        self.client_id = client_id
        self.args = args
        self.start_order_id = start_order_id
        self.logger = logging.getLogger(__name__)

        self.debug_mode = False
        if args.debug:
            self.debug_mode = True

        self.logger.info(
            f'Starting with args -'
            f' symbol: {self.args.symbol},'
            f' order_type: {self.args.order_type},'
            f' quote_type: {self.args.quote_type},'
            f' order_size: {self.args.order_size},'
            f' bar_period: {self.args.bar_period}')

        self.logfile_candles = 'logs/log_candles.csv'
        logfile_candles_rows = ('time', 'symbol', 'open', 'high', 'low', 'close', 'ha_open', 'ha_close', 'ha_high', 'ha_low', 'ha_color')
        self.logfile_orders = 'logs/log_orders.csv'
        logfile_orders_rows = ('time', 'order_id', 'symbol', 'side', 'order_type', 'size', 'price')
        if not os.path.isdir('logs/'):
            os.makedirs('logs')
        if not os.path.exists(self.logfile_candles):
            self._write_csv_row((logfile_candles_rows,), self.logfile_candles, newfile=True)
        if not os.path.exists(self.logfile_orders):
            self._write_csv_row((logfile_orders_rows,), self.logfile_orders, newfile=True)

        self.candle_calc_use_prev_ha = True
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
        self.best_bid = None
        self.best_ask = None
        self.last = None # Last trade price, as received from RealTimeBars

        self.cancel_enable = False

        self.contract = self._create_contract_obj()

        #
        if not hasattr(self, 'mktData_reqId'):
            # First time init of object
            self.mktData_reqId = random.randint(0, 999)
        if not hasattr(self, 'rtBars_reqId'):
            # First time init of object
            while True:
                self.rtBars_reqId = random.randint(0, 999)
                if not self.rtBars_reqId == self.mktData_reqId:
                    break

        if not self.debug_mode:
            # Connect to server and start feeds
            self._connect()
            self._cancel_orders()
            self._subscribe_mktData()
            self._subscribe_rtBars()
        else:
            # Run test setup here
            pass
            #self._connect()
            #self._test_setup()
            #self.reqOpenOrders()

        if not hasattr(self, 'order_id'):
            # Allow for obj to re __init__() and not reset self.order_id
            if self.start_order_id is not None:
                self.order_id = self.start_order_id
            else:
                self._update_order_id()
                #self.order_id = 0 # Placeholder. Will get updated prior to any order, via _place_order()
        else:
            pass
            #self._update_order_id()

    def error(self, reqId, errorCode, errorString):
        self.logger.warning(f'{codes(errorCode)}, {errorCode}, {errorString}')

    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType == 1 and reqId == self.mktData_reqId:
            # Bid
            self.best_bid = price
            self.logger.info(f'Bid update: {price}')
        if tickType == 2 and reqId == self.mktData_reqId:
            # Ask
            self.best_ask = price
            self.logger.info(f'Ask update: {price}')
        if tickType == 4 and reqId == self.mktData_reqId:
            # Last
            self.last = price
            self.logger.info(f'Last trade update: {price}')

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        #self.order_id = orderId
        #logger.info(f'The next valid order id is: {self.order_id}')

    def orderStatus(
	    self, orderId, status, filled, remaining, avgFullPrice,
	    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        self.logger.info(
            f'orderStatus - orderid: {orderId}, status: {status}'
            f'filled: {filled}, remaining: {remaining}'
            f'lastFillPrice: {lastFillPrice}')

    def openOrder(self, orderId, contract, order, orderState):
        if self.cancel_enable:
            self.logger.warning(
                f'Canceling Order: {orderId}, {contract.symbol}, {contract.secType},'
                f'@, {contract.exchange}, {order.action}, {order.orderType},'
                f'{order.totalQuantity}, {orderState.status}')
            self.cancelOrder(orderId)
            return
        self.logger.info(
            f'openOrder id: {orderId}, {contract.symbol}, {contract.secType},'
            f'@, {contract.exchange}, {order.action}, {order.orderType},'
            f'{order.totalQuantity}, {orderState.status}')

    def openOrderEnd(self):
        """This is called at the end of a given request for open orders."""
        super().openOrderEnd()
        self.cancel_enable = False

    def execDetails(self, reqId, contract, execution):
        self.logger.info(
            f'Order Executed: {reqId}, {contract.symbol},'
            f'{contract.secType}, {contract.currency}, {execution.execId},'
            f'{execution.orderId}, {execution.shares}, {execution.lastLiquidity}')

    def historicalData(self, reqId, bar):
        self.logger.info(
            f'HistoricalData: {reqId}, Date: {bar.date},'
            f'Open: {bar.open}, High: {bar.high},'
            f'Low: {bar.low}, Close: {bar.close}')

    def realtimeBar(self, reqId, time, open_, high, low, close, volume, wap, count):
        super().realtimeBar(reqId, time, open_, high, low, close, volume, wap, count)
        self._tohlc = (time, open_, high, low, close)
        self.logger.info('--')
        self.logger.info(
            f'RealTimeBar. TickerId: {reqId},'
            f'{dt.datetime.fromtimestamp(time)}, -1,'
            f'{self._tohlc[1:]}, {volume}, {wap}, {count}')
        #
        if self.last and self.best_bid and self.best_ask:
            # Don't start processing data until we get the first msgs from data feed
            self._on_update()

    def _run(self):
        self.run()

    def _cancel_orders(self, cycle_all=False):
        # 2 methods below for canceling orders

        if cycle_all:
            # Cycle through all possible orders from this session and cancel
            for _id in range(self.start_order_id, self.order_id + 1):
                self.cancelOrder(_id)
        else:
            self.cancel_enable = True
            self.reqOpenOrders() # openOrder() will receive all open orders and do cancelOrder() there

    def _connect(self):
        self.logger.info(f'port: {self.args.port}, client_id {self.client_id}')
        self.connect("127.0.0.1", self.args.port, self.client_id)
        while not self.isConnected():
            self.logger.info(f'Connecting to IB.. {self.args.symbol}, {self.client_id}')
            time.sleep(0.5)
        self.logger.info(f'Connected - {self.args.symbol}, {self.client_id}')

    def _disconnect(self):
        self.disconnect()
        while self.isConnected():
            self.logger.info(f'Disconnecting from IB.. {self.args.symbol}, {self.client_id}')
            time.sleep(0.5)
        self.logger.info(f'Disconnected - {self.args.symbol}, {self.client_id}')

    def _subscribe_mktData(self):
        self.reqMktData(self.mktData_reqId, self.contract, '', False, False, [])

    def _subscribe_rtBars(self):
        self.reqRealTimeBars(
            self.rtBars_reqId,
            self.contract,
            self.RT_BAR_PERIOD,
            "MIDPOINT", False, [])

    def _on_update(self):
        # Process 5s updates as received
        self._cache_update(self._tohlc)
        if self._check_period():
            # On HA candle tick point
            self._update_candles()
            self.cache = []
            if self.candles.shape[0] > 0:
                #
                self._check_order_conditions()

    def _check_period(self):
        # Return True if period ends at this update, else False
        _time = dt.datetime.fromtimestamp(self._tohlc[0])
        total_secs = _time.hour*3600 + _time.minute*60 + _time.second
        if total_secs % self.period == 0:
            return True
        return False

    def _write_csv_row(self, row, filename, newfile=False):
        if newfile:
            mode = 'w'
        else:
            mode = 'a'
        with open(filename, mode) as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerows(row)

    def _update_candles(self):
        # Bar completed
        if self.cache[-1][0] - self.cache[0][0] + self.RT_BAR_PERIOD == self.period:
            # Hit the candle period boundary. Update HA candles dataframe
            _pd = self._calc_new_candle()
            self.candles = self.candles.append(_pd, ignore_index=True)
            #
            bar_color = None
            bar_color_prev = None
            if self.candles.shape[0] > 1:
                bar_color = self.candles['ha_color'].values[-1].upper()
#            else:
#                # First HA candle not yet available
#                return
            if self.candles.shape[0] > 2:
                bar_color_prev = self.candles['ha_color'].values[-2].upper()
            self.logger.info('--')
            self.logger.warning(f'Candle: {self.cache[-1][0]}, {self.args.symbol} - {bar_color}, Prev: {bar_color_prev}')
            csv_row = [col[1] for col in _pd.items()]
            csv_row.insert(1, self.args.symbol)
            self._write_csv_row((csv_row,), self.logfile_candles)
        elif self.cache[-1][0] - self.cache[0][0] + self.RT_BAR_PERIOD < self.period:
            # First iteration. Not enough updates for a full period
            self.logger.info('Not enough data for a candle')
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
            ha_c = (ohlc[0] + ohlc[1] + ohlc[2] + ohlc[3])/4
            if self.candle_calc_use_prev_ha:
                if self.candles.shape[0] == 1:
                    # No prior HA candle is available, use prev raw candle open/close
                    ha_o = (self.candles['open'].values[-1] + self.candles['close'].values[-1])/2
                else:
                    ha_o = (self.candles['ha_open'].values[-1] + self.candles['ha_close'].values[-1])/2
            else:
                ha_o = (self.candles['open'].values[-1] + self.candles['close'].values[-1])/2
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

    def _cache_update(self, tohlc):
        # Still in the middle of a period. Cache data for processing at end of period
        self.cache.append(tohlc)

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
            order_obj = self._place_order(_side)
            self.first_order = False
            self.order_size *= 2
        elif not self.candles['ha_color'].values[-1] == self.candles['ha_color'].values[-2]:
            order_obj = self._place_order(_side)
        else:
            # Candle color same as previous. Do not place an order
            return
        pr = order_obj.lmtPrice if order_obj.orderType == 'LMT' else None
        csv_row = (order_obj.timestamp, order_obj.order_id, self.args.symbol, _side, order_obj.orderType, order_obj.totalQuantity, pr)
        self._write_csv_row((csv_row,), self.logfile_orders)

    def _test_setup(self):
        # Sandbox to set up test env
        self._create_test_order()
        self._create_test_order()
        self._create_test_order()
        self._create_test_order()
        self._create_test_order()

    def _create_test_order(self, side='Buy'):
        # Easy way to tweak order obj params to create contrived test orders
        obj = self._create_order_obj(side)
        obj.orderType = 'LMT'
        obj.lmtPrice = round(0.01 + random.randint(1,100)/100, 2)
        self._place_order('Buy', order_obj=obj)

    def _place_order(self, side, order_obj=None):
        if not order_obj:
            order_obj = self._create_order_obj(side)
        self.logger.warning(f'Order: {order_obj.order_id}, {self.contract.symbol}, {order_obj.action}, {order_obj.orderType}, {order_obj.totalQuantity}, {order_obj.lmtPrice}')
        self.placeOrder(order_obj.order_id, self.contract, order_obj)
        return order_obj

    def _update_order_id(self):
        # The proper way to do this is call self.reqIds(), but have seen issues here
        # Use manual option for now
        if self.start_order_id is not None:
            self.order_id += 1
        else:
            #
            _order_id = self.order_id
            self.reqIds(1) # Initiate update to self.order_id, will be done in nextValidId()
            if _order_id == self.order_id:
                time.sleep(5.0) # Give time for self.order_id to update

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
            # Note here: self.order_type can deviate from self.args.order_type
            order.orderType = self.order_type = 'LMT'
            order.outsideRth = True # Do to avoid seeing warning msg
        else:
            order.orderType = self.order_type = self.args.order_type
        price = 0
        if self.order_type == 'LMT':
            order.sweepToFill = True
            if self.args.quote_type == 'mid':
                price = round((self.best_bid + self.best_ask)/2, 2)
            elif self.args.quote_type == 'last':
                price = self.last
        order.lmtPrice = price
        #
        self._update_order_id()
        order.order_id = self.order_id
        order.timestamp = dt.datetime.now().timestamp()
        return order

    def _create_contract_obj(self):
        contract = Contract()
        contract.symbol = self.args.symbol
        contract.secType = self.args.security_type
        contract.exchange = self.args.exchange
        contract.currency = self.args.currency
        return contract

def main_cli(args):
    # For running the app from the command line

    while True:
        clientIds = list({random.randint(0, 999) for _ in args.symbol})
        if len(clientIds) == len(args.symbol):
            break
    objs = {}
    for i, instr in enumerate(args.symbol):
        _args = copy.deepcopy(args)
        _args.symbol = instr
        objs[instr] = MarketDataApp(clientIds[i], _args, start_order_id=1000*i)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.symbol)) as executor:
        futures = []
        for instr in args.symbol:
            futures.append(executor.submit(objs[instr]._run))

def parse_args():
    argp = argparse.ArgumentParser()
    argp.add_argument("symbol", type=str, default=None, nargs='+')
    argp.add_argument(
        "-l", "--loglevel", type=str, default='warning', help="Logging options: debug/info/warning"
    )
    argp.add_argument(
        "-d", "--debug", action='store_const', const=True, default=False, help="Run in debug mode. MarketDataApp will init but not start feeds. And open up a debugger"
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
        "-o", "--order-type", type=str, default='MKT', help="Order type (MKT/LMT)"
    )
    argp.add_argument(
        "-q", "--quote-type", type=str, default='last', help="Quote type (mid/last). Only used with LMT order type"
    )

    args = argp.parse_args()
    return args

if __name__ == "__main__":
    args = parse_args()

    logfile = 'logs/IB_trader.log'
    if args.loglevel == 'debug':
        logging.basicConfig(filename=logfile, level=logging.DEBUG)
    elif args.loglevel == 'info':
        logging.basicConfig(filename=logfile, level=logging.INFO)
    elif args.loglevel == 'warning':
        logging.basicConfig(filename=logfile, level=logging.WARNING)
    main_cli(args)
