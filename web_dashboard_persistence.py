# -*- coding: utf-8 -*-
import time
import random
import logging
import argparse
import threading
import dash
import dash_daq as daq
import dash_core_components as dcc
import dash_html_components as html
from dash.dependencies import Input, Output, State

from IB_trader import MarketDataApp

external_stylesheets = ['https://codepen.io/chriddyp/pen/bWLwgP.css']

app = dash.Dash(__name__, external_stylesheets=external_stylesheets, title='Trader')

MAX_INSTRUMENTS = 100

argp = argparse.ArgumentParser()
argp.add_argument(
    "-l", "--loglevel", type=str, default='warning', help="Logging options: debug/info/warning"
)
args = argp.parse_args()

if args.loglevel == 'info':
    logging.basicConfig(level=logging.INFO)
elif args.loglevel == 'warning':
    logging.basicConfig(level=logging.WARNING)
else:
    raise ValueError

class Object(object):
    pass

class TraderAction:
    def __init__(self, loglevel):
        self.loglevel = loglevel
        self.state = {}
        self.port = None # 7496/7497 for TWS prod/paper, 4001/4002 for Gateway prod/paper
        self.initial_thread = True # After first thread starts, set to False

    def updates(self, instrument):
        if not self.state.get(instrument[0]):
            # First time start up for this instrument/symbol
            self.state[instrument[0]] = {}
            self.state[instrument[0]]['args'] = instrument[1:]
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
        if not self.port:
            # Handling for edge case, when restart script but previous browser state has row data, triggering callback
            return
        if self.state[instrument].get('client'):
            # Reconnect. Re-init MarketDataApp() obj to reconnect in existing thread
            if not self.state[instrument]['thread'].is_alive:
                logging.info(f"WEB: Thread for {instrument}, {self.state[instrument]['clientId']} is down")
                raise Exception
            _args = self._make_args(instrument)
            self.state[instrument]['client'].__init__(self.state[instrument]['clientId'], _args)
            self.state[instrument]['client'].run()
        else:
            # First time connecting. Start new thread and init MarketDataApp() obj
            _args = self._make_args(instrument)
            self.state[instrument]['client'] = MarketDataApp(self.state[instrument]['clientId'], _args)
            if self.initial_thread:
                # On startup, cancel any active unfilled orders account-wide
                self.state[instrument]['client'].reqGlobalCancel()
            self.state[instrument]['thread'] = threading.Thread(target=self.state[instrument]['client'].run, daemon=True) 
            self.state[instrument]['thread'].start()

    def _stop(self, instrument, stop_thread=False):
        # Cancel any outstanding orders
        self.state[instrument]['client']._cancel_orders()

        # First do a disconnect with the server
        self.state[instrument]['client']._disconnect()
        
        if stop_thread:
            # Stop thread
            # ... not implemented
            while True:
                if not self.state[instrument]['thread'].is_alive:
                    break
                else:
                    logging.info(f'WEB: Waiting for thread to stop: {instrument}')
                    time.sleep(0.5)
            logging.info(f'WEB: Thread stopped: {instrument}')

    def _make_args(self, instrument):
        args = Object()
        args.currency = 'USD'
        args.loglevel = 'info'
        args.debug = False
        args.exchange = 'SMART'
        args.port = self.port
        args.security_type = 'STK'
        args.symbol = instrument
        args.order_size = int(self.state[instrument]['args'][0])
        args.bar_period = int(self.state[instrument]['args'][1])
        args.order_type = self.state[instrument]['args'][2][:3]
        if self.state[instrument]['args'][2][4:] in ('last', 'mid'):
            args.quote_type = self.state[instrument]['args'][2][4:]
        else:
            args.quote_type = 'last'
        return args

trader_action = TraderAction(args.loglevel)

#
def instrument_rows(row_num, display='inline-block', persistence=True):
    row = [
        dcc.Input(
            id=f'{row_num}-row-input-symbol',
            type='text',
            value='',
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display}
        ),
        dcc.Input(
            id=f'{row_num}-row-input-size',
            type='text',
            value='',
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display}
        ),
        dcc.Input(
            id=f'{row_num}-row-input-period',
            type='text',
            value='',
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
            value=None,
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display},
        ),
        daq.BooleanSwitch(
            id=f'{row_num}-row-input-start-stop',
            on=False,
            persistence_type='memory',
            persistence=persistence,
            style={'width': '100px', 'display': display},
        ),
    ]
    return row

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
            style={'width': '35%'}
	),
    ]),
    html.Br(),
    html.Br(),
    html.Table(id='rows-content'),
    html.Br(),
    html.Button(id='add-instrument-row', n_clicks=0, children='Add instrument'),

    # Hidden table to give an output target to update_instruments' callback
    html.Table([html.Tr([html.Td(c, style={'display': 'none'}) for c in instrument_rows(n, display='none')]) for n in range(0, MAX_INSTRUMENTS)]),
])

def dynamic_rows(num_rows):
    table = [
        html.Tr([html.Th(c, style={'width': '150px'}) for c in ('Symbol', 'Size', 'Bar period (s)', 'Order type', 'Start/Stop')])
    ] + [
        html.Tr([html.Td(c, style={'width': '150px'}) for c in instrument_rows(n, persistence=True)])
        for n in range(0, num_rows)
    ]
    return table

@app.callback(
            Output('tcp-port', 'data'),
            [Input('paper-live-dropdown', 'value')],)
def update_port(value):
    if value == 'paper':
        _val = 4002
    elif value == 'live':
        _val = 4001
    else:
        raise ValueError
    trader_action.port = _val
    return _val

@app.callback(
            Output('rows-content', 'children'),
            [Input('add-instrument-row', 'n_clicks')],)
def draw_rows(n_clicks):
    table = dynamic_rows(n_clicks)
    return table

@app.callback(Output('session-state', 'data'),
            [Input(f'{n}-row-input-start-stop', 'on') for n in range(0, MAX_INSTRUMENTS)],
            [State(f'{n}-row-input-symbol', 'value') for n in range(0, MAX_INSTRUMENTS)]
            + [State(f'{n}-row-input-size', 'value') for n in range(0, MAX_INSTRUMENTS)]
            + [State(f'{n}-row-input-period', 'value') for n in range(0, MAX_INSTRUMENTS)]
            + [State(f'{n}-row-input-order-type', 'value') for n in range(0, MAX_INSTRUMENTS)]
            + [State(f'{n}-row-input-start-stop', 'on') for n in range(0, MAX_INSTRUMENTS)])
def update_instruments(start_stop, *state):
    instruments = get_instrument_config(state)
    for instrument in instruments:
        trader_action.updates((instrument,) + instruments[instrument])
    return instruments


def get_instrument_config(state):
    # Parse raw state from update_instruments to get instrument config 
    offset = MAX_INSTRUMENTS
    instruments = {}
    if state:
        for i in range(offset - 1, 2*offset - 1):
            if state[i]:
                instruments[state[i]] = (state[i+offset], state[i+2*offset], state[i+3*offset], state[i+4*offset])
    else:
        instruments = ''
    return instruments

if __name__ == '__main__':
    app.run_server(debug=True)
