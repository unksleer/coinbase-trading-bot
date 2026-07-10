from config.settings import Settings
from coinbase.rest import RESTClient

def main():
    client = RESTClient(api_key=Settings.COINBASE_API_KEY, api_secret=Settings.COINBASE_API_SECRET)
    response = client.get_accounts()
    print("Type of response:", type(response))
    print("Directory of response:", dir(response))
    if hasattr(response, 'to_dict'):
        d = response.to_dict()
        print("to_dict type:", type(d))
        print("to_dict keys:", d.keys() if isinstance(d, dict) else "Not a dict")
        print("Sample account representation:", str(response)[:300])

if __name__ == '__main__':
    main()
