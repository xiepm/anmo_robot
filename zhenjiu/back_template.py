"""Back candidates from observed shoulders/hips, NOT vertebra detection.

Names/nominal vertebral levels follow published anatomical descriptions:
https://doi.org/10.1145/3777577.3777630
https://pmc.ncbi.nlm.nih.gov/articles/PMC11353071/
The image fractions below are engineering priors, not anatomical measurements
or WHO B-cun conversions. Preserve the original C7=.05 / L2=.60 preview anchors.
No training or point-level ground truth is claimed.
"""

# name, code, nominal level (C7=0, Tn=n, Ln=12+n), bilateral
BACK_SPECS = (
    ('大椎', 'GV-14', 0, False),
    ('身柱', 'GV-12', 3, False),
    ('肺俞', 'BL-13', 3, True),
    ('心俞', 'BL-15', 5, True),
    ('至阳', 'GV-9', 7, False),
    ('膈俞', 'BL-17', 7, True),
    ('肝俞', 'BL-18', 9, True),
    ('脾俞', 'BL-20', 11, True),
    ('命门', 'GV-4', 14, False),
    ('肾俞', 'BL-23', 14, True),
    ('大肠俞', 'BL-25', 16, True),
    # Append rather than renumber the existing 18 live-preview candidates.
    ('腰阳关', 'GV-3', 16, False),
)
EXTRA_SPECS = (('肩井', 'GB-21'), ('天宗', 'SI-11'), ('风池', 'GB-20'))
BACK_NAMES = {s[0] for s in BACK_SPECS} | {s[0] for s in EXTRA_SPECS}
BACK_IDS = {key: i+1 for i, key in enumerate(
    [(name, side) for name, _, _, paired in BACK_SPECS
     for side in (('L','R') if paired else (None,))]
    + [(name, side) for name, _ in EXTRA_SPECS for side in ('L', 'R')])}


def candidate(body, side, level, paired):
    if not body.visible(11,12,23,24):
        return None
    # Suppress collapsed or flipped shoulder/hip assignments, not fill missing
    # landmarks from previous frames. Fractions cannot recover invisible anatomy.
    if min(body.shoulder_width,body.hip_width,body.trunk_len) < .08:
        return None
    a, b = body.p[11], body.p[12]
    c, d = body.p[23], body.p[24]
    if (a[0]-b[0])*(c[0]-d[0])+(a[1]-b[1])*(c[1]-d[1]) <= 0:
        return None
    fraction = .05 + level*(.60-.05)/14
    x,y = body.torso_center(fraction)
    if paired:
        dx,dy = body.side_dir(side,fraction)
        width = body.torso_width(fraction)*.16
        x,y = x+dx*width,y+dy*width
    return x,y


def extra_candidate(body, side, name):
    """Image candidates, not detection of C7/acromion/scapula/occiput.

    Nominal identities: WHO Standard Acupuncture Point Locations (2008),
    GB21, SI11, GB20. The fractions below are explicit engineering priors,
    NOT WHO measurements. Neck candidates additionally require current ears;
    missing head landmarks must never be reconstructed from the torso alone.
    """
    c7 = candidate(body, side, 0, False)
    if c7 is None:
        return None
    if name == '肩井':
        shoulder = body.p[11 if side == 'L' else 12]
        return tuple((a+b)/2 for a,b in zip(c7, shoulder))
    if name == '天宗':
        fraction = .05 + 4*(.60-.05)/14
        x,y = body.torso_center(fraction)
        dx,dy = body.side_dir(side, fraction)
        width = body.torso_width(fraction)*.30
        return x+dx*width, y+dy*width
    if name == '风池':
        if not body.visible(7,8):
            return None
        ears = body.ear_mid
        # Reject a head on the hip side, collapsed neck, or swapped ear sides.
        up = ((body.sh_mid[0]-ears[0])*body.trunk_dir[0]
              +(body.sh_mid[1]-ears[1])*body.trunk_dir[1])
        ear_span = (body.p[7][0]-body.p[8][0], body.p[7][1]-body.p[8][1])
        shoulder_span = (body.p[11][0]-body.p[12][0], body.p[11][1]-body.p[12][1])
        if not .03*body.trunk_len < up < .65*body.trunk_len:
            return None
        if sum(a*b for a,b in zip(ear_span, shoulder_span)) <= 0:
            return None
        ear = body.p[7 if side == 'L' else 8]
        center = tuple(.8*a+.2*b for a,b in zip(ears, c7))
        return tuple(a+.5*(b-c) for a,b,c in zip(center, ear, ears))
    return None


def rules():
    result=[]
    for name,code,level,paired in BACK_SPECS:
        result.append(dict(name=name,code=code,tier='低',paired=paired,
            region='back',views={'back'},localization_method='pose_proportion_estimate',
            anatomical_location_validated=False,
            fn=lambda b,s,level=level,paired=paired: candidate(b,s,level,paired)))
    for name,code in EXTRA_SPECS:
        result.append(dict(name=name,code=code,tier='低',paired=True,
            region='back',views={'back'},localization_method='pose_proportion_estimate',
            anatomical_location_validated=False,
            fn=lambda b,s,name=name: extra_candidate(b,s,name)))
    return result
