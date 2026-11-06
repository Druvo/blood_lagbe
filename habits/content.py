"""Facts and myth-or-fact questions for the daily micro-actions.

General knowledge only, hedged on purpose. Nothing here is medical advice,
and every fact ends with the same reminder: the blood bank's screening
decides who can donate. The wording follows the Learn page (age 18 to 60,
at least 45 kg, a recovery gap that differs between blood banks).
"""
from django.conf import settings
from django.utils.translation import ngettext

FACT_FOOTER = "The blood bank's screening decides eligibility."


def interval_label():
    """Same label as the `donation_interval_label` template variable."""
    days = settings.DONATION_INTERVAL_DAYS
    months = round(days / 30)
    if days % 30 == 0:
        return ngettext('%(n)s month', '%(n)s months', months) % {'n': months}
    return ngettext('%(n)s day', '%(n)s days', days) % {'n': days}


# Each entry is a template: {interval} becomes the site's recovery gap.
_FACTS = [
    'Many blood banks ask donors to be between 18 and 60 years old. The exact limits can differ from one blood bank to the next.',
    'A common minimum weight to donate is about 45 kg. Your blood bank will weigh you on the day if they need to.',
    'We use {interval} as a reminder gap between donations. Blood banks may ask for a longer or shorter wait.',
    'A whole-blood donation itself usually takes around ten minutes. Registration, the health check and a rest afterwards make the visit longer, often up to an hour.',
    'Eating a normal meal and drinking water before you go is usually recommended. Donating on an empty stomach can make some people feel faint.',
    'Blood banks usually ask a few health questions and do a quick check, often pulse, blood pressure and a small finger-prick test, before every donation.',
    'Feeling unwell on the day, with a fever, cold or infection, is a common reason a blood bank asks someone to come back later. Waiting is a sensible choice.',
    'Tattoos, piercings, recent travel and some medicines can mean a short wait before donating. Tell the staff and let them decide.',
    'People who are pregnant or have recently given birth are usually asked to wait. The length of the wait is for the blood bank to advise.',
    'Every blood group is needed. Group O- red cells suit many patients, so O- donors are always welcome, but patients usually receive their own group first.',
    'Patients with AB+ blood can usually receive red cells from all groups. Hospitals always cross-match blood before using it.',
    'Licensed blood banks use sterile, single-use needles for each donor. If you are curious, you can ask the staff to show you.',
    'Women can donate if they pass the same checks as everyone else. Some blood banks ask women to wait longer between donations.',
    'Most donors feel a quick pinch when the needle goes in and little after that. Tell the staff if you feel dizzy at any point.',
    'After donating, many people are told to drink extra fluids, eat well and take it easy for the rest of the day. Follow the advice your blood bank gives.',
    'Bringing an ID is commonly asked for at registration. Check with your blood bank about what they accept.',
    'Stored red cells have a limited shelf life, so hospitals need blood to arrive steadily over time. That never means you should rush: resting between donations matters.',
    'Blood is also separated into parts, such as red cells, plasma and platelets, and different patients may need different parts. The hospital decides what each patient receives.',
    'A single donation is often described as helping more than one patient because the parts can be used separately. Any people-helped number is an estimate, not a promise.',
    'Haemoglobin is checked before donating because it shows whether you have enough red cell protein. If it is low on the day, the staff may ask you to wait and eat well.',
    'Iron-rich foods such as lentils, leafy greens, eggs and meat can be part of a balanced diet for regular donors. Your doctor can advise you personally.',
    'If you take regular medicines, mention them to the blood bank. Some are fine and some mean a wait, and only the staff can tell you which.',
    'Not being able to donate right now does not make you any less helpful. Sharing an urgent request, or confirming your availability, can reach someone who can.',
    'Being deferred is common and often temporary. It is the blood bank looking after both you and the patient.',
    'Blood banks and hospitals often run donation camps. Opening hours and requirements differ, so check with the organiser before you travel.',
    'Donating with a friend or family member can make the first visit feel easier. Each person is still screened on their own.',
    'Wearing something with sleeves that roll up easily helps on the day. Staff usually need access to the upper arm.',
    'After a donation, many blood banks suggest avoiding heavy lifting and hard exercise until the next day. Ask your blood bank what they recommend.',
    'Plan your day so you can rest for a short while afterwards and are not rushing. Do not drive if you feel dizzy or unwell.',
    'If you feel unwell hours or days after donating, contact the blood bank and follow their advice.',
    'Hospitals match patient and donor blood groups carefully. A donor group that usually matches is a guide, and the hospital decides in each case.',
    'Urgent requests are time sensitive. Even when you cannot give, passing a request on to a friend who matches can help it reach the right person sooner.',
    'Many people donate for the first time because someone asked them to. A kind, pressure-free invitation is often enough.',
    'It is fine to say no, or not yet. A donation should always be your own free choice, at a time when you feel well.',
    'Keeping a note of your last donation date helps you and your blood bank plan around your recovery gap.',
    'Donating blood does not give you a medical check-up. If you have health questions, a doctor is the right person to ask.',
    'Blood banks rely on voluntary donors giving regularly over time. Your own blood bank can tell you which groups are needed most near you.',
]


def facts():
    """All facts, ready to show, in a fixed order."""
    label = interval_label()
    return [text.format(interval=label) for text in _FACTS]


# (statement, is_fact, explanation). Shown as "myth or fact?".
_QUIZ = [
    ('Donating blood makes you weak for weeks.', False,
     'Myth. Most healthy people feel fine after resting and drinking fluids, and the body replaces what was given over the following days and weeks.'),
    ('Licensed blood banks use a new sterile needle for every donor.', True,
     'Fact. Sterile, single-use equipment is standard practice. You can ask the staff if you want to see it opened.'),
    ('Donating blood hurts a lot.', False,
     'Myth. Most people feel a quick pinch when the needle goes in and little else.'),
    ('Only people with O blood are needed.', False,
     'Myth. Every group is needed. O- red cells suit many patients, but patients usually receive their own group first.'),
    ('It is a good idea to skip meals before donating.', False,
     'Myth. A normal meal and some water beforehand is usually advised. An empty stomach can make some people feel faint.'),
    ('Women can donate blood.', True,
     'Fact. Women can donate if they pass the same checks. Some blood banks ask women to wait longer between donations.'),
    ('You can donate again the very next week.', False,
     'Myth. Your body needs time to recover. We use {interval} as a reminder gap, and blood banks may ask for a different one.'),
    ('Staff check your health before every donation.', True,
     'Fact. Blood banks ask health questions and do a quick check, such as pulse, blood pressure and a finger-prick test, each time.'),
    ('A donation itself usually takes about ten minutes.', True,
     'Fact. The donation is short. The whole visit, with registration, checks and a rest afterwards, can take up to an hour.'),
    ('If you have a cold or fever, it is fine to donate anyway.', False,
     'Myth. Feeling unwell is a common reason to wait. Come back when you are well, and let the blood bank decide.'),
    ('Being turned away at a blood bank means something is seriously wrong.', False,
     'Myth. Many deferrals are short and routine, such as a low reading on the day or a recent trip. Ask the staff what they advise.'),
    ('You can help save lives even if you cannot donate yourself.', True,
     'Fact. Sharing an urgent request, spreading accurate information or encouraging a friend can all help someone get blood sooner.'),
    ('Anyone of any age can donate blood.', False,
     'Myth. Blood banks set age limits, commonly 18 to 60, and they can differ. Staff check your eligibility on the day.'),
    ('It helps to drink plenty of fluids after donating.', True,
     'Fact. Extra fluids, a proper meal and a quiet rest of the day are commonly suggested. Follow your blood bank\'s advice.'),
    ('Any compatible blood group can be given without testing.', False,
     'Myth. Hospitals cross-match blood before using it. A compatibility table is a guide, not a substitute.'),
    ('You should drive straight home and do hard exercise after donating.', False,
     'Myth. Most blood banks suggest a short rest first and gentler activity for the rest of the day. Do not drive if you feel dizzy.'),
    ('People helped per donation is an estimate, not a guarantee.', True,
     'Fact. One donation may be split into parts used by different patients, but any number you see is an estimate.'),
]


def quiz_bank():
    label = interval_label()
    return [
        {
            'statement': statement,
            'is_fact': is_fact,
            'answer': 'F' if is_fact else 'M',
            'explanation': explanation.format(interval=label),
        }
        for statement, is_fact, explanation in _QUIZ
    ]
