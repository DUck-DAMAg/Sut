[app]
title = Суть
package.name = sut
package.domain = org.example
source.dir = .
source.include_exts = py
version = 0.1

requirements = python3,kivy,requests,urllib3,idna,certifi,charset-normalizer,beautifulsoup4,soupsieve,typing_extensions

orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
