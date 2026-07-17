import requests
import base64
import threading


def fetch_dicebear_avatar(seed, style="pixel-art"):
    base_url = f"https://api.dicebear.com/7.x/{style}/png"
    url = f"{base_url}?seed={seed}"
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes
        base64_data = base64.b64encode(response.content).decode("utf-8")
        data_url = f"data:image/png;base64,{base64_data}"
        return data_url
    except requests.exceptions.RequestException as e:
        print(f"Error fetching avatar for seed '{seed}': {e}")
        return None


def fetch_dicebear_avatars(seeds, style="pixel-art"):
    avatar_dataurls = {}
    threads = []

    def worker(seed):
        data_url = fetch_dicebear_avatar(seed, style)
        avatar_dataurls[seed] = data_url

    for seed in seeds:
        thread = threading.Thread(target=worker, args=(seed,))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    return avatar_dataurls

