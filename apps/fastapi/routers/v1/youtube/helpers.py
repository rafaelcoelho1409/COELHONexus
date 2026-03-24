from pytubefix.contrib.search import Filter

from schemas.inputs import YouTubeSearchConfig


def build_filters(config: YouTubeSearchConfig) -> Filter:
    # Enum mappings
    upload_date_map = {
        "Last Hour": Filter.UploadDate.LAST_HOUR,
        "Today": Filter.UploadDate.TODAY,
        "This Week": Filter.UploadDate.THIS_WEEK,
        "This Month": Filter.UploadDate.THIS_MONTH,
        "This Year": Filter.UploadDate.THIS_YEAR,
    }  
    duration_map = {
        "Under 4 minutes": Filter.Duration.UNDER_4_MINUTES,
        "4 - 20 minutes": Filter.Duration.BETWEEN_4_20_MINUTES,
        "Over 20 minutes": Filter.Duration.OVER_20_MINUTES,
    }  
    type_map = {
        "Video": Filter.Type.VIDEO,
        "Channel": Filter.Type.CHANNEL,
        "Playlist": Filter.Type.PLAYLIST,
        "Movie": Filter.Type.MOVIE,
    }  
    sort_by_map = {
        "Relevance": Filter.SortBy.RELEVANCE,
        "Upload Date": Filter.SortBy.UPLOAD_DATE,
        "View count": Filter.SortBy.VIEW_COUNT,
        "Rating": Filter.SortBy.RATING,
    }  
    features_map = {
        "Live": Filter.Features.LIVE,
        "4K": Filter.Features._4K,
        "HD": Filter.Features.HD,
        "Subtitles/CC": Filter.Features.SUBTITLES_CC,
        "Creative Commons": Filter.Features.CREATIVE_COMMONS,
        "360": Filter.Features._360,
        "VR180": Filter.Features.VR180,
        "3D": Filter.Features._3D,
        "HDR": Filter.Features.HDR,
        "Location": Filter.Features.LOCATION,
        "Purchased": Filter.Features.PURCHASED,
    }  
    # Build filter using fluent API
    filters = Filter.create()  
    if config.upload_date:
        filters = filters.upload_date(upload_date_map[config.upload_date])   
    if config.video_type:
        filters = filters.type(type_map[config.video_type])  
    if config.duration:
        filters = filters.duration(duration_map[config.duration])
    if config.sort_by:
        filters = filters.sort_by(sort_by_map[config.sort_by])   
    if config.features:
        feature_enums = [features_map[f] for f in config.features]
        filters = filters.feature(feature_enums)
    return filters