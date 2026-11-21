import math


COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
ARROWS = ("↑", "↗", "→", "↘", "↓", "↙", "←", "↖")


def guidance(position, destination, radius):
    dx, dz = (target - origin for origin, target in zip(position, destination))
    distance = math.hypot(dx, dz)
    if distance <= radius:
        return "Destination reached!"
    index = round(math.atan2(dx, -dz) / (math.pi / 4)) % len(COMPASS)
    return f"{ARROWS[index]}  {COMPASS[index]} · {math.ceil(distance):,} blocks"


def world_key(config):
    return (config["edition"], config["seed"], config["dimension"])
