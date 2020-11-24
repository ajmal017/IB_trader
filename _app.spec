# -*- mode: python ; coding: utf-8 -*-

import sys ; sys.setrecursionlimit(sys.getrecursionlimit() * 5)

block_cipher = None


a = Analysis(['app.py'],
             pathex=['/Users/david/Desktop/Dave/consulting/Upwork/projs/IB_trading_candles/dev/IB_trader'],
             binaries=[],
             datas=[
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash/favicon.ico', 'dash'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash/*.js', 'dash'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash_daq/package-info.json', 'dash_daq'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash_daq/metadata.json', 'dash_daq'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash_daq/*.js', 'dash_daq'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash_core_components/package-info.json', 'dash_core_components'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash_core_components/*.js', 'dash_core_components'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash_html_components/package-info.json', 'dash_html_components'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash_html_components/*.js', 'dash_html_components'),
                 ('/opt/miniconda3/lib/python3.7/site-packages/dash_renderer/*.js', 'dash_renderer'),
                 ('logs/IB_trader.log', 'logs'),
                 ('logs/log_candles.csv', 'logs'),
                 ('logs/log_orders.csv', 'logs'),
             ],
             hiddenimports=[],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='app',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True )
