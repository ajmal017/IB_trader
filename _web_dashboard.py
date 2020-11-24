#!/usr/local/bin/python3

# -*- coding: utf-8 -*-
import time
import random
import logging
import argparse
import threading
import monkey_patch
import dash
import dash_daq as daq
import dash_core_components as dcc
import dash_html_components as html
from dash.dependencies import Input, Output, State

from IB_trader import MarketDataApp

MAX_INSTRUMENTS = 100

def main():
    external_stylesheets = ['https://codepen.io/chriddyp/pen/bWLwgP.css']

    app = dash.Dash(__name__, external_stylesheets=external_stylesheets, title='Trader')

    #MAX_INSTRUMENTS = 100

    argp = argparse.ArgumentParser()
    argp.add_argument(
        "-l", "--loglevel", type=str, default='warning', help="Logging options: debug/info/warning"
    )
    args = argp.parse_args()

    logfile = 'logs/IB_trader.log'
    if args.loglevel == 'info':
        logging.basicConfig(filename=logfile, level=logging.INFO)
    elif args.loglevel == 'warning':
        logging.basicConfig(filename=logfile, level=logging.WARNING)
        #logging.basicConfig(level=logging.WARNING)
    else:
        raise ValueError

    trader_action = TraderAction(args.loglevel)

    # Layout
    app.layout = html.Div([
        dcc.Store(id='session-state'),
        dcc.Store(id='tcp-port'),
        html.H1(
            children='Trade Terminal',
            style={
                'textAlign': 'center',
            }
        ),
        html.Div([
            html.Label(
                [
                    "Account type",
                    dcc.Dropdown(
                        id='paper-live-dropdown',
                        options=[
                            {'label': 'Live', 'value': 'live'},
                            {'label': 'Paper', 'value': 'paper'},
                        ],
                        placeholder="Account Type",
                        value='paper',
                        clearable=False,
                        persistence=False,
                        style={'width': '120px'}
                    ),
                ], style={'width': '150px', 'display': 'inline-block'},
            ),

            html.Label(
                [
                    "Connection type",
                    dcc.Dropdown(
                        id='connection-type',
                        options=[
                            {'label': 'Gateway', 'value': 'gateway'},
                            {'label': 'TWS', 'value': 'tws'},
                        ],
                        placeholder="Connection type",
                        value='gateway',
                        clearable=False,
                        persistence=False,
                        style={'width': '120px'}
                    ),
                ], style={'width': '150px', 'display': 'inline-block'},
            ),
        ], style={'width': '70%'}),
        html.Br(),
        html.Br(),
        html.Table(id='rows-content'),
        html.Br(),
        html.Button(id='add-instrument-row', n_clicks=0, children='Add instrument'),

        # Hidden table to give an output target to update_instruments' callback
        html.Table([html.Tr([html.Td(c, style={'display': 'none'}) for c in instrument_rows(n, display='none')]) for n in range(0, MAX_INSTRUMENTS)]),
    ])

    # Callbacks
    @app.callback(
                Output('tcp-port', 'data'),
                [Input('paper-live-dropdown', 'value'),
                Input('connection-type', 'value')],)
    def update_port(paper_live, connection_type):
        if paper_live == 'paper':
            if connection_type == 'gateway':
                _val = 4002
            elif connection_type == 'tws':
                _val = 7497
        elif paper_live == 'live':
            if connection_type == 'gateway':
                _val = 4001
            elif connection_type == 'tws':
                _val = 7496
            _val = 4001
        else:
            raise ValueError
        trader_action.port = _val
        return _val

    @app.callback(Output('session-state', 'data'),
                [Input('add-instrument-row', 'n_clicks')]
                + [Input(f'{n}-row-input-start-stop', 'on') for n in range(0, MAX_INSTRUMENTS)],
                [State(f'{n}-row-input-symbol', 'value') for n in range(0, MAX_INSTRUMENTS)]
                + [State(f'{n}-row-input-size', 'value') for n in range(0, MAX_INSTRUMENTS)]
                + [State(f'{n}-row-input-period', 'value') for n in range(0, MAX_INSTRUMENTS)]
                + [State(f'{n}-row-input-order-type', 'value') for n in range(0, MAX_INSTRUMENTS)]
                + [State(f'{n}-row-input-start-stop', 'on') for n in range(0, MAX_INSTRUMENTS)])
    def update_instruments(n_clicks, *startstop_rows):
        ctx = dash.callback_context
        start_stop = startstop_rows[:MAX_INSTRUMENTS]
        rows = startstop_rows[MAX_INSTRUMENTS:]

        instruments = get_instrument_config(rows, MAX_INSTRUMENTS)
        if '-row-input-start-stop' in ctx.triggered[0]['prop_id']:
            i = int(ctx.triggered[0]['prop_id'].split('-')[0])
            instrument = rows[i]
            _instruments = {i[0]:i[1:] for i in instruments}
            trader_action.updates((instrument,) + _instruments[instrument])

        rows = state_to_rows(trader_action.state, MAX_INSTRUMENTS)

        if ctx.triggered[0]['prop_id'] == 'add-instrument-row.n_clicks':
            rows = rows + (True,)

        return rows

    @app.callback(
                Output('rows-content', 'children'),
                [Input('session-state', 'data'),])
    def update_rows(data):
        instruments = get_instrument_config(data, MAX_INSTRUMENTS)
        if instruments == []:
            instruments = [('', '', '', None, False)]
        if len(data) == 5*MAX_INSTRUMENTS + 1:
            instruments.append(('', '', '', None, False))
        table = draw_table(instruments, MAX_INSTRUMENTS)
        return table

    app.run_server(debug=False)

class Object(object):
    pass

class ApplicationLogicError(Exception):
    pass

# Object used for interacting with IB_trader
class TraderAction:
    order_id_offset = 1000
    def __init__(self, loglevel):
        self.loglevel = loglevel
        self.state = {}
        self.port = None # 7496/7497 for TWS prod/paper, 4001/4002 for Gateway prod/paper
        self.initial_thread = True # After first thread starts, set to False
        self.next_order_id_start = 0
        self.logger = logging.getLogger()

    def updates(self, instrument):
        if not self.state.get(instrument[0]):
            # First time start up for this instrument/symbol
            self.state[instrument[0]] = {}
            self.state[instrument[0]]['args'] = instrument[1:]
            self.state[instrument[0]]['order_id_start'] = self.next_order_id_start
            self.next_order_id_start += TraderAction.order_id_offset
            self.state[instrument[0]]['clientId'] = self._get_new_clientId()
            self._start(instrument[0])
            return
        if not self.state[instrument[0]]['args'][-1] == instrument[-1]:
            # Start/Stop button flipped from previous state
            self.state[instrument[0]]['args'] = instrument[1:]
            if self.state[instrument[0]]['args'][-1]:
                self._start(instrument[0])
            else:
                self._stop(instrument[0])

    def _get_new_clientId(self):
        while True:
            _id = random.randint(0, 2*MAX_INSTRUMENTS)
            if not _id in {self.state[c].get('clientId') for c in self.state}:
                break
        return _id

    def _start(self, instrument):
        self.logger.warning(f"Connecting - {instrument}, {self.state[instrument]['clientId']}")
        if self.state[instrument].get('thread') and self.state[instrument]['thread'].is_alive:
            # Reconnect. Re-init MarketDataApp() obj to reconnect in existing thread
            _args = self._make_args(instrument)
            self.state[instrument]['client'].__init__(self.state[instrument]['clientId'], _args, start_order_id=0)
            self.state[instrument]['client']._run()
        else:
            # First time connecting. Start new thread and init MarketDataApp() obj
            _args = self._make_args(instrument)
            self.state[instrument]['client'] = MarketDataApp(self.state[instrument]['clientId'], _args, start_order_id=self.next_order_id_start)
            if self.initial_thread:
                self.initial_thread = False
                # On startup, cancel any active unfilled orders account-wide
                self.state[instrument]['client'].reqGlobalCancel()
            self.state[instrument]['thread'] = threading.Thread(target=self.state[instrument]['client']._run, daemon=True)
            self.state[instrument]['thread'].start()

    def _stop(self, instrument, stop_thread=False):
        # Cancel any outstanding orders
        self.state[instrument]['client']._cancel_orders()

        # First do a disconnect with the server
        self.state[instrument]['client']._disconnect()

        self.logger.warning(f"Disconnected - {instrument}, {self.state[instrument]['clientId']}")
        
        if stop_thread:
            # Stop thread
            # ... not implemented
            while True:
                if not self.state[instrument]['thread'].is_alive:
                    break
                else:
                    self.logger.info(f'WEB: Waiting for thread to stop: {instrument}')
                    time.sleep(0.5)
            self.logger.warning(f'WEB: Thread stopped: {instrument}')


    def _make_args(self, instrument):
        args = Object()
        args.currency = 'USD'
        args.loglevel = 'info'
        args.debug = False
        args.exchange = 'SMART'
        args.port = self.port
        args.security_type = 'STK'
        args.symbol = instrument
        args.order_size = self.state[instrument]['args'][0]
        args.bar_period = int(self.state[instrument]['args'][1]*60)
        args.order_type = self.state[instrument]['args'][2][:3]
        if self.state[instrument]['args'][2][4:] in ('last', 'mid'):
            args.quote_type = self.state[instrument]['args'][2][4:]
        else:
            args.quote_type = 'last'
        return args

# Utility functions
def draw_table(data, len_table):
    table = [
        html.Tr([html.Th(c, style={'width': '150px'}) for c in ('Symbol', 'Size', 'Bar period (m)', 'Order type', 'Start/Stop')])
    ] + [
        html.Tr([html.Td(c, style={'width': '150px', 'display': 'none'}) for c in instrument_rows(n, display='none')])
        if n >= len(data)
        else
        html.Tr([html.Td(c, style={'width': '150px'}) for c in instrument_rows(n, data=data[n])])
        for n in range(len_table)
    ]
    return table

def get_instrument_config(state, offset):
    # Parse raw state from update_instruments to get instrument config
    instruments = []
    if state:
        for i in range(offset):
            if state[i]:
                instruments.append((state[i], state[i+offset], state[i+2*offset], state[i+3*offset], state[i+4*offset]))
    else:
        instruments = ''
    return instruments

def state_to_rows(state, offset):
    rows = ['' for _ in range(3*offset)] + [None for _ in range(offset)] + [False for _ in range(offset)]
    for i, instrument in enumerate(state):
        rows[i] = instrument
        rows[i + offset] = state[instrument]['args'][0]
        rows[i + 2*offset] = state[instrument]['args'][1]
        rows[i + 3*offset] = state[instrument]['args'][2]
        rows[i + 4*offset] = state[instrument]['args'][3]
    return tuple(rows)

def instrument_rows(row_num, data=('', '', '', None, False), display='inline-block', persistence=False):
    row = [
        dcc.Input(
            id=f'{row_num}-row-input-symbol',
            type='text',
            value=data[0],
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display}
        ),
        dcc.Input(
            id=f'{row_num}-row-input-size',
            type='number',
            value=data[1],
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display}
        ),
        dcc.Input(
            id=f'{row_num}-row-input-period',
            type='number',
            value=data[2],
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display}
        ),
        dcc.Dropdown(
            id=f'{row_num}-row-input-order-type',
            options=[
                {'label': 'MKT', 'value': 'MKT'},
                {'label': 'LMT (last)', 'value': 'LMT_last'},
                {'label': 'LMT (mid)', 'value': 'LMT_mid'},
            ],
            value=data[3],
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display},
        ),
        daq.BooleanSwitch(
            id=f'{row_num}-row-input-start-stop',
            on=data[4],
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display},
        ),
    ]
    return row

if __name__ == '__main__':
    main()
