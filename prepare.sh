#!/bin/bash
# Prepare source files for black box build
set -e
cd /home/ubuntu/blackbox_build
rm -rf src_lib build dist engine
mkdir -p src_lib

DIR=/home/ubuntu/openalgo/sumit_strategies/lib

# ENGINE FILES — will be compiled to .so (strategy logic protected)
cp $DIR/pr_engine.py src_lib/
cp $DIR/pr_runner.py src_lib/
cp $DIR/analyze_broker.py src_lib/
cp $DIR/hedging.py src_lib/
cp $DIR/openalgo_gateway.py src_lib/
cp $DIR/gamma_engine.py src_lib/     # Gamma (long_gamma_rescue) strategy logic
cp $DIR/delta_engine.py src_lib/     # Delta (short strangle) strategy logic

# SUPPORT FILES — keep as plain .py (no strategy logic to hide)
cp $DIR/events.py src_lib/
cp $DIR/clock.py src_lib/
cp $DIR/telegram_bot.py src_lib/
cp $DIR/commands.py src_lib/
cp $DIR/checkpoint.py src_lib/
cp $DIR/paper_broker.py src_lib/
cp $DIR/panic.py src_lib/
cp $DIR/data_recorder.py src_lib/
cp $DIR/tick_validator.py src_lib/
cp $DIR/order_retry.py src_lib/
cp $DIR/nse_data.py src_lib/

echo "Files ready:"
ls src_lib/
