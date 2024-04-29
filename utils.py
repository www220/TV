try:
    import user_config as config
except ImportError:
    import config
import aiohttp
import asyncio
import time
import re
import datetime
import os
import urllib.parse
import ipaddress
from urllib.parse import urlparse
import requests
import re


def getChannelItems():
    """
    Get the channel items from the source file
    """
    # Open the source file and read all lines.
    try:
        user_source_file = (
            "user_" + config.source_file
            if os.path.exists("user_" + config.source_file)
            else getattr(config, "source_file", "demo.txt")
        )
        with open(user_source_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Create a dictionary to store the channels.
        channels = {}
        current_category = ""
        pattern = r"^(.*?),(?!#genre#)(.*?)$"

        for line in lines:
            line = line.strip()
            if "#genre#" in line:
                # This is a new channel, create a new key in the dictionary.
                current_category = line.split(",")[0]
                channels[current_category] = {}
            else:
                # This is a url, add it to the list of urls for the current channel.
                match = re.search(pattern, line)
                if match is not None:
                    if match.group(1) not in channels[current_category]:
                        channels[current_category][match.group(1)] = [match.group(2)]
                    elif (
                        match.group(2)
                        and match.group(2)
                        not in channels[current_category][match.group(1)]
                    ):
                        channels[current_category][match.group(1)].append(
                            match.group(2)
                        )
        return channels
    finally:
        f.close()


async def getChannelsByExtendBaseUrls(channel_names):
    """
    Get the channels by extending the base urls
    """
    channels = {}
    headers = {"User-Agent": "okhttp/3.15"}
    pattern = r"^(.*?),(?!#genre#)(.*?)$"
    sub_pattern = r"_\((.*?)\)|_\[(.*?)\]|频道"
    for base_url in config.extend_base_urls:
        try:
            print(f"Processing extend base url: {base_url}")
            try:
                response = requests.get(base_url, headers=headers, timeout=30)
            except requests.exceptions.Timeout:
                print(f"Timeout on {base_url}")
                continue
            content = response.text
            if content:
                lines = content.split("\n")
                link_dict = {}
                for line in lines:
                    if re.match(pattern, line) is not None:
                        key = re.match(pattern, line).group(1)
                        resolution_match = re.search(r"_(\((.*?)\))", key)
                        resolution = (
                            resolution_match.group(2)
                            if resolution_match is not None
                            else None
                        )
                        key = re.sub(sub_pattern, "", key).lower()
                        url = re.match(pattern, line).group(2)
                        value = (url, None, resolution)
                        if key in link_dict:
                            link_dict[key].append(value)
                        else:
                            link_dict[key] = [value]
                found_channels = []
                for channel_name in channel_names:
                    sub_channel_name = re.sub(sub_pattern, "", channel_name).lower()
                    values = link_dict.get(sub_channel_name)
                    if values:
                        if channel_name in channels:
                            channels[channel_name] += values
                        else:
                            channels[channel_name] = values
                        found_channels.append(channel_name)
                if found_channels:
                    print(f"{base_url} found channels: {','.join(found_channels)}")
        except Exception as e:
            print(f"Error on {base_url}: {e}")
            continue
    print("Finished processing extend base urls")
    return channels


def updateChannelUrlsTxt(cate, channelUrls):
    """
    Update the category and channel urls to the final file
    """
    try:
        with open("result_new.txt", "a", encoding="utf-8") as f:
            f.write(cate + ",#genre#\n")
            for name, urls in channelUrls.items():
                for url in urls:
                    if url is not None:
                        f.write(name + "," + url + "\n")
            f.write("\n")
    finally:
        f.close


def updateFile(final_file, old_file):
    """
    Update the file
    """
    if os.path.exists(old_file):
        os.replace(old_file, final_file)


def getUrlInfo(result):
    """
    Get the url, date and resolution
    """
    url = date = resolution = None
    result_div = [div for div in result.children if div.name == "div"]
    if 1 < len(result_div):
        channel_text = result_div[1].get_text(strip=True)
        url_match = re.search(
            r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
            channel_text,
        )
        if url_match is not None:
            url = url_match.group()
        info_text = result_div[-1].get_text(strip=True)
        if info_text:
            date, resolution = (
                (info_text.partition(" ")[0] if info_text.partition(" ")[0] else None),
                (
                    info_text.partition(" ")[2].partition("•")[2]
                    if info_text.partition(" ")[2].partition("•")[2]
                    else None
                ),
            )
    return url, date, resolution


async def getSpeed(url, urlTimeout=5):
    """
    Get the speed of the url
    """
    async with aiohttp.ClientSession() as session:
        start = time.time()
        try:
            async with session.get(url, timeout=urlTimeout) as response:
                resStatus = response.status
        except:
            return float("inf")
        end = time.time()
        if resStatus == 200:
            return int(round((end - start) * 1000))
        else:
            return float("inf")


async def sortUrlsBySpeedAndResolution(infoList):
    """
    Sort by speed and resolution
    """
    response_times = await asyncio.gather(*(getSpeed(url) for url, _, _ in infoList))
    valid_responses = [
        (info, rt) for info, rt in zip(infoList, response_times) if rt != float("inf")
    ]

    def extract_resolution(resolution_str):
        numbers = re.findall(r"\d+x\d+", resolution_str)
        if numbers:
            width, height = map(int, numbers[0].split("x"))
            return width * height
        else:
            return 0

    default_response_time_weight = 0.5
    default_resolution_weight = 0.5
    response_time_weight = getattr(
        config, "response_time_weight", default_response_time_weight
    )
    resolution_weight = getattr(config, "resolution_weight", default_resolution_weight)
    # Check if weights are valid
    if not (
        0 <= response_time_weight <= 1
        and 0 <= resolution_weight <= 1
        and response_time_weight + resolution_weight == 1
    ):
        response_time_weight = default_response_time_weight
        resolution_weight = default_resolution_weight

    def combined_key(item):
        (_, _, resolution), response_time = item
        resolution_value = extract_resolution(resolution) if resolution else 0
        return (
            -(response_time_weight * response_time)
            + resolution_weight * resolution_value
        )

    sorted_res = sorted(valid_responses, key=combined_key, reverse=True)
    return sorted_res


def filterByDate(data):
    """
    Filter by date and limit
    """
    default_recent_days = 60
    use_recent_days = getattr(config, "recent_days", 60)
    if (
        not isinstance(use_recent_days, int)
        or use_recent_days <= 0
        or use_recent_days > 365
    ):
        use_recent_days = default_recent_days
    start_date = datetime.datetime.now() - datetime.timedelta(days=use_recent_days)
    recent_data = []
    unrecent_data = []
    for (url, date, resolution), response_time in data:
        if date:
            date = datetime.datetime.strptime(date, "%m-%d-%Y")
            if date >= start_date:
                recent_data.append(((url, date, resolution), response_time))
            else:
                unrecent_data.append(((url, date, resolution), response_time))
    if len(recent_data) < config.urls_limit:
        recent_data.extend(unrecent_data[: config.urls_limit - len(recent_data)])
    else:
        for i in range(len(recent_data)):
            (url, date, resolution), response_time = recent_data[i]
            if is_ipv6(url):
                if i >= config.urls_limit:
                    recent_data[config.urls_limit-1] = recent_data[i]
                break
    return recent_data[: config.urls_limit]


def getTotalUrls(data):
    """
    Get the total urls with filter by date and depulicate
    """
    total_urls = []
    if len(data) > config.urls_limit:
        total_urls = [url for (url, _, _), _ in filterByDate(data)]
    else:
        total_urls = [url for (url, _, _), _ in data]
    return list(dict.fromkeys(total_urls))


def is_ipv6(url):
    """
    Check if the url is ipv6
    """
    try:
        host = urllib.parse.urlparse(url).hostname
        ipaddress.IPv6Address(host)
        return True
    except ValueError:
        return False


def checkUrlIPVType(url):
    """
    Check if the url is compatible with the ipv type in the config
    """
    ipv_type = getattr(config, "ipv_type", "ipv4")
    if ipv_type == "ipv4":
        return not is_ipv6(url)
    elif ipv_type == "ipv6":
        return is_ipv6(url)
    else:
        return True


def checkByDomainBlacklist(url):
    """
    Check by domain blacklist
    """
    domain_blacklist = [
        urlparse(domain).netloc if urlparse(domain).scheme else domain
        for domain in getattr(config, "domain_blacklist", [])
    ]
    return urlparse(url).netloc not in domain_blacklist


def checkByURLKeywordsBlacklist(url):
    """
    Check by URL blacklist keywords
    """
    url_keywords_blacklist = getattr(config, "url_keywords_blacklist", [])
    return not any(keyword in url for keyword in url_keywords_blacklist)


def checkUrlByPatterns(url):
    """
    Check the url by patterns
    """
    return (
        checkUrlIPVType(url)
        and checkByDomainBlacklist(url)
        and checkByURLKeywordsBlacklist(url)
    )


def filterUrlsByPatterns(urls):
    """
    Filter urls by patterns
    """
    urls = [url for url in urls if checkUrlIPVType(url)]
    urls = [url for url in urls if checkByDomainBlacklist(url)]
    urls = [url for url in urls if checkByURLKeywordsBlacklist(url)]
    return urls


async def useAccessibleUrl():
    """
    Check if the url is accessible
    """
    baseUrl1 = "https://www.foodieguide.com/iptvsearch/"
    baseUrl2 = "http://tonkiang.us/"
    speed1 = await getSpeed(baseUrl1, 30)
    speed2 = await getSpeed(baseUrl2, 30)
    if speed1 == float("inf") and speed2 == float("inf"):
        return None
    if speed1 < speed2:
        return baseUrl1
    else:
        return baseUrl2
