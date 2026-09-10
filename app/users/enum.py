import enum

class UserRank(str, enum.Enum):
    ROOKIE = "rookie"
    AMATEUR = "amateur"
    PRO = "pro"
    WORLD_CLASS = "world_class"
    CHAMPION = "champion"

class F1Teams(str, enum.Enum):
    MERCEDES = "mercedes"
    FERRARI = "ferrari"
    RED_BULL = "red_bull"
    MCLAREN = "mclaren"
    ALPINE = "alpine"
    ASTON_MARTIN = "aston_martin"
    RACING_BULLS = "racing_bulls"
    HAAS = "haas"
    WILLIAMS = "williams"
    AUDI = "audi"
    CADILLAC = "cadillac"