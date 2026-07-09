import pandas as pd
from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def get_required_candles_count(self) -> int:
        """
        Returns the minimum number of historical candles needed to evaluate this strategy.
        """
        pass

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> str:
        """
        Evaluates the strategy based on historical candle data.
        
        Parameters:
        - df: A pandas DataFrame containing historical candle data.
              Must contain 'close' column, and should be ordered by time ascending.
              
        Returns:
        - "BUY" if buy signal is triggered.
        - "SELL" if sell signal is triggered.
        - "HOLD" / "NONE" if no signals are triggered.
        """
        pass
