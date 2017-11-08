import json

from rhubarbe.singleton import Singleton
from rhubarbe.config import Config

class InventoryPhones(metaclass = Singleton):
    """
    A class for loading and storing the phones specifications
    typically from /etc/rhubarbe/inventory-phones.json
    """

    def __init__(self):
        the_config = Config()
        try:
            with open(the_config.value('testbed', 'inventory_phones_path')) as feed:
                self._phones = json.load(feed)
        except FileNotFoundError:
            self._phones = []

    def all_phones(self):
        """
        For now just return the json contents as is
        """
        return self._phones
