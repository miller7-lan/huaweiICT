"""Scope gate for flow V3: privacy, region and current validity, before retrieval.

frozen_qa.scope_gate (kept unchanged for the earlier chains) matches 18 consecutive digits,
31 province names plus 南京, and eight words for "now". This gate keeps the same three
outcomes and messages and closes the recorded gaps:

- an ID number split by spaces, hyphens or dots is joined on a private copy before matching;
- prefecture-level cities outside Henan are recognized (a few names that are also common
  words, such as 中山, 朝阳 or 来宾, are left out on purpose; see OMITTED_CITIES);
- a region named only to exclude it ("不涉及江苏") does not stop a Henan question;
- "还有效吗", "是否已废止", "还能照此办理" ask about validity even without 现在/今天.

Like the first gate it is lexical: it does not resolve every way of naming a place or of
asking about validity, and it errs toward stopping.
"""
import re

from gov_mcp.frozen_qa import CLAUSE, CLAUSE_QUESTION, CURRENT, DISCLAIMER, OTHER_REGIONS


ID_SEPARATORS = re.compile(r"(?<=[\dXx])[\s\-－—_.·]+(?=[\dXx])")
PERSONAL_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?![\dXx])")

# Prefecture-level divisions outside Henan, without 市/州/盟.
CITIES = (
    "石家庄 唐山 秦皇岛 邯郸 邢台 保定 张家口 承德 沧州 廊坊 衡水 太原 阳泉 晋城 朔州 晋中 运城 忻州 临汾 吕梁 "
    "呼和浩特 包头 乌海 赤峰 通辽 鄂尔多斯 呼伦贝尔 巴彦淖尔 乌兰察布 兴安盟 锡林郭勒 阿拉善 "
    "沈阳 大连 鞍山 抚顺 本溪 丹东 锦州 营口 阜新 辽阳 盘锦 铁岭 葫芦岛 长春 辽源 通化 白山 松原 白城 延边 "
    "哈尔滨 齐齐哈尔 鸡西 鹤岗 双鸭山 大庆 伊春 佳木斯 七台河 牡丹江 黑河 绥化 大兴安岭 "
    "无锡 徐州 常州 苏州 南通 连云港 淮安 盐城 扬州 镇江 泰州 宿迁 "
    "杭州 宁波 温州 嘉兴 湖州 绍兴 金华 衢州 舟山 台州 丽水 "
    "合肥 芜湖 蚌埠 淮南 马鞍山 淮北 铜陵 安庆 滁州 阜阳 宿州 六安 亳州 池州 宣城 "
    "福州 厦门 莆田 三明 泉州 漳州 南平 龙岩 宁德 南昌 景德镇 萍乡 九江 新余 鹰潭 赣州 吉安 宜春 抚州 上饶 "
    "济南 青岛 淄博 枣庄 东营 烟台 潍坊 济宁 泰安 威海 临沂 德州 聊城 滨州 菏泽 "
    "武汉 黄石 十堰 宜昌 襄阳 鄂州 荆门 孝感 荆州 黄冈 咸宁 随州 恩施 仙桃 潜江 神农架 "
    "长沙 株洲 湘潭 衡阳 邵阳 岳阳 常德 张家界 益阳 郴州 永州 怀化 娄底 湘西 "
    "广州 韶关 深圳 珠海 汕头 佛山 江门 湛江 茂名 肇庆 惠州 梅州 汕尾 河源 阳江 清远 东莞 潮州 揭阳 云浮 "
    "南宁 柳州 桂林 梧州 防城港 钦州 贵港 玉林 百色 贺州 河池 崇左 海口 三亚 三沙 儋州 "
    "成都 自贡 攀枝花 泸州 德阳 绵阳 广元 遂宁 内江 乐山 南充 眉山 宜宾 广安 达州 雅安 巴中 资阳 阿坝 甘孜 凉山 "
    "贵阳 六盘水 遵义 安顺 毕节 铜仁 黔东南 黔南 黔西南 "
    "昆明 曲靖 玉溪 保山 昭通 丽江 临沧 楚雄 西双版纳 大理 德宏 怒江 迪庆 "
    "拉萨 日喀则 昌都 林芝 那曲 西安 铜川 宝鸡 咸阳 渭南 延安 汉中 榆林 商洛 "
    "兰州 嘉峪关 金昌 天水 武威 张掖 平凉 酒泉 庆阳 定西 陇南 临夏 甘南 西宁 海东 "
    "银川 石嘴山 吴忠 固原 中卫 乌鲁木齐 克拉玛依 吐鲁番 哈密 昌吉 博尔塔拉 巴音郭楞 阿克苏 克孜勒苏 喀什 "
    "和田 伊犁 塔城 阿勒泰 石河子 香港 澳门 台湾"
).split()
# Also common words or names; matching them would stop ordinary Henan questions.
OMITTED_CITIES = ("中山", "朝阳", "来宾", "日照", "白银", "阿里", "大同", "四平", "长治", "安康", "红河", "文山",
                  "山南", "玉树", "海北", "海西", "黄南", "果洛", "普洱", "北海", "天门")
REGIONS = tuple(dict.fromkeys(OTHER_REGIONS + tuple(CITIES)))
REGION = re.compile("|".join(sorted(map(re.escape, REGIONS), key=len, reverse=True)))
# "不涉及江苏、浙江", "与江苏无关", "除江苏外": the region is excluded, not asked about.
REGION_LIST = rf"(?:{REGION.pattern})(?:\s*(?:[、和与及或]|以及)\s*(?:{REGION.pattern}))*"
EXCLUDED = re.compile(
    rf"(?:不涉及|不包括|不包含|不含|不问|不讨论|不考虑|不针对|不是|并非|非|排除)\s*(?:关于)?\s*{REGION_LIST}"
    rf"|与\s*{REGION_LIST}\s*无关|除(?:了)?\s*{REGION_LIST}\s*(?:以)?外"
)

ONGOING = re.compile(r"还有效|仍有效|仍然有效|继续有效|是否有效|是不是有效|有效吗|有没有效|还适用|仍适用|仍然适用"
                     r"|还能照|还能按|还可以按|还可以照|还在执行|仍在执行|还执行|还算数|还作数")
# A certificate-loss declaration is a document, not a question about a rule's validity.
STATUS = re.compile(r"废止|失效|作废(?!声明|公告|申报)|废除|停止执行")
STATUS_QUESTION = re.compile(r"吗|么|是否|是不是|有没有|[？?]")


def private_id(question):
    return bool(PERSONAL_ID.search(ID_SEPARATORS.sub("", question)))


def outside_region(question):
    return bool(REGION.search(EXCLUDED.sub(" ", question)))


def current_validity_asked(question):
    """A clause about today or about whether the rule still holds, other than a pure disclaimer."""
    for clause in CLAUSE.split(question):
        clause = clause.strip()
        asked = (any(word in clause for word in CURRENT) or ONGOING.search(clause)
                 or (STATUS.search(clause) and STATUS_QUESTION.search(clause)))
        if not asked:
            continue
        if DISCLAIMER.match(clause) and not CLAUSE_QUESTION.search(clause):
            continue
        return True
    return False


def scope_gate(question):
    if private_id(question):
        return "PRIVATE_FACT_NOT_ACCEPTED"
    if outside_region(question):
        return "OUTSIDE_HENAN_SCOPE"
    if current_validity_asked(question):
        return "CURRENT_VALIDITY_NOT_ESTABLISHED"
    return None
