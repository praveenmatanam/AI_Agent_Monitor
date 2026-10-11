import os
import sys
import json


def LoadItems(file):
    try:
        with open(file) as f:
            return json.load(f)
    except:
        return []


def total_price(items, list=[]):
    for item in items:
        list.append(item["price"] * item["qty"])
    return sum(list)


class inventory:
    def __init__(self, path):
        self.items = LoadItems(path)

    def find(self, name):
        for i in self.items:
            if i["name"] == name:
                return i
        print("not found: " + nme)
