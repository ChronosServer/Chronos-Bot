def save_config(self):
        with open("config.json", "r+") as f:
            cfg = json.load(f)
            cfg["storage"] = self.storage_dict
            f.seek(0)
            f.write(json.dumps(cfg, indent=4))
            f.truncate()