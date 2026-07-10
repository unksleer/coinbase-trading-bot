import sys
from config.settings import Settings
from coinbase.rest import RESTClient

def main():
    if not Settings.COINBASE_API_KEY or not Settings.COINBASE_API_SECRET:
        print("API keys not set.")
        sys.exit(1)
        
    client = RESTClient(api_key=Settings.COINBASE_API_KEY, api_secret=Settings.COINBASE_API_SECRET)
    print("Class methods/properties of client.market_order_buy:")
    # We can inspect the SDK classes directly
    try:
        from coinbase.rest.types.orders import CreateOrderResponse
        print("CreateOrderResponse attributes:", dir(CreateOrderResponse))
    except Exception as e:
        print("Error importing CreateOrderResponse:", e)

if __name__ == '__main__':
    main()
