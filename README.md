localization script including pre-populated english translations for Silent Talk vrchat plugin

https://booth.pm/ja/items/7436818

```shell
git clone https://github.com/butrejp/silent-talk-en-localization.git
cd silent-talk-en-localization
pip install -r requirements.txt
python render_en.py all
```
it outputs to `./render_en/*.png`.  you can simply copy these over into the assets folder, overwriting the originals
