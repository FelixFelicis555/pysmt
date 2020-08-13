#
# This file is part of pySMT.
#
#   Copyright 2014 Andrea Micheli and Marco Gario
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#

from pysmt.logics import LRA, LIA


from pysmt.exceptions import (SolverReturnedUnknownResultError,
                              PysmtUnboundedOptimizationError,
                              GoalNotSupportedError)
from pysmt.optimization.optimizer import SUAOptimizerMixin, IncrementalOptimizerMixin
from pysmt.optimization.optimizer import Optimizer

from pysmt.solvers.msat import MSatEnv, MathSAT5Model, MathSATOptions
from pysmt.solvers.msat import MathSAT5Solver, MSatConverter, MSatQuantifierEliminator
from pysmt.solvers.msat import MSatInterpolator, MSatBoolUFRewriter

from pysmt.solvers.dynmsat import MSATCreateEnv

# TODO:
# - check msat does not instantiate any MSAT class directly (use virtual override)
# - is it possible to reintroduce file-level try-except for library import?
# - the "Not in Python's Path" message is wrong for MathSAT when only OptiMAthSAT
#   is installed.. the current implementation must be revised.

class OptiMSATEnv(MSatEnv):
    __lib_name__ = "optimathsat"

    def __init__(self, msat_config=None):
        MSatEnv.__init__(self, msat_config=msat_config)

    def _do_create_env(self, msat_config=None, msat_env=None):
        return self._msat_lib.msat_create_opt_env(msat_config, msat_env)

    def _do_create_env(self, msat_config=None, msat_env=None):
        return self._msat_lib.msat_create_opt_env(msat_config, msat_env)


class OptiMSATModel(MathSAT5Model):
    __lib_name__ = "optimathsat"

    def __init__(self, environment, msat_env):
        MathSAT5Model.__init__(self, environment=environment,
                               msat_env=msat_env)


class OptiMSATOptions(MathSATOptions):
    __lib_name__ = "optimathsat"

    def __init__(self, **base_options):
        MathSATOptions.__init__(self, **base_options)


class OptiMSATSolver(MathSAT5Solver, Optimizer):
    __lib_name__ = "optimathsat"

    LOGICS = MathSAT5Solver.LOGICS

    OptionsClass = OptiMSATOptions

    def __init__(self, environment, logic, **options):
        MathSAT5Solver.__init__(self, environment=environment,
                                logic=logic, **options)

    def _le(self, x, y):
        # TODO: support FP
        # TODO: support signed/unsigned BV optimization
        otype = self.environment.stc.get_type(x)
        mgr = self.environment.formula_manager
        if otype.is_int_type() or otype.is_real_type():
            return mgr.LE(x, y)
        elif otype.is_bv_type():
            return mgr.BVULE(x, y)

    def optimize(self, goal, **kwargs):

        if goal.is_minmax_goal() or goal.is_maxmin_goal():
            if goal.is_minmax_goal():
                make_fun = self._msat_lib.msat_make_minmax
            else:
                make_fun = self._msat_lib.msat_make_maxmin

            cost_function = goal.terms
            obj_fun = []
            for f in cost_function:
                obj_fun.append(self.converter.convert(f))
            msat_obj = make_fun(self.msat_env(), obj_fun, False)
        elif goal.is_minimization_goal() or goal.is_maximization_goal():
            if goal.is_minimization_goal():
                make_fun = self._msat_lib.msat_make_minimize
            else:
                make_fun = self._msat_lib.msat_make_maximize

            cost_function = goal.term()
            obj_fun = self.converter.convert(cost_function)
            msat_obj = make_fun(self.msat_env(), obj_fun, False)
        else:
            raise GoalNotSupportedError("optimathsat", goal)

        self._msat_lib.msat_assert_objective(self.msat_env(), msat_obj)
        self.solve()
        optres = self._msat_lib.msat_objective_result(self.msat_env(), msat_obj)
        if optres == self._msat_lib.MSAT_OPT_UNKNOWN:
            raise SolverReturnedUnknownResultError()
        elif optres == self._msat_lib.MSAT_OPT_UNSAT:
            return None
        else:
            unbounded = self._msat_lib.msat_objective_value_is_unbounded(self.msat_env(),
                                                                  msat_obj,
                                                                  self._msat_lib.MSAT_OPTIMUM)
            if unbounded > 0:
                raise PysmtUnboundedOptimizationError("The optimal value is unbounded")

            is_strict = self._msat_lib.msat_objective_value_is_strict(self.msat_env(),
                                                                      msat_obj,
                                                                      self._msat_lib.MSAT_OPTIMUM)
            if is_strict:
                raise PysmtUnboundedOptimizationError("The optimal value is infinitesimal")

            check = self._msat_lib.msat_load_objective_model(self.msat_env(), msat_obj)
            if check != 0:
                raise ValueError()

            model = self.get_model()
            return model, optres

    def pareto_optimize(self, goals):
        self._msat_lib.msat_set_opt_priority(self.environment, "par")
        msat_objs = []

        for g in goals:
            if g.is_minmax_goal() or g.is_maxmin_goal():
                if g.is_minmax_goal():
                    f = self._msat_lib.msat_make_minmax
                else:
                    f = self._msat_lib.msat_make_maxmin

                cost_function = g.terms
                obj_fun = []
                for f in cost_function:
                    obj_fun.append(self.converter.convert(f))
            elif g.is_minimization_goal() or g.is_maximization_goal():
                if g.is_minimization_goal():
                    f = self._msat_lib.msat_make_minimize
                else:
                    f = self._msat_lib.msat_make_maximize

                cost_function = g.term()
                obj_fun = self.converter.convert(cost_function)
            else:
                raise GoalNotSupportedError("optimathsat", g)

            msat_obj = f(self.msat_env(), obj_fun)
            msat_objs.append(msat_obj)
            self._msat_lib.msat_assert_objective(self.msat_env(), msat_obj)

        models = {}
        for goal in goals:
            rt = self.solve()
            if rt:
                model = self.get_model()
                models[goal] = (model, model.get_value(goal.term()))
            else:
                return None


    def lexicographic_optimize(self, goals):
        self._msat_lib.msat_set_opt_priority(self.environment, "lex")
        msat_objs = []

        for g in goals:
            if g.is_minmax_goal() or g.is_maxmin_goal():
                if g.is_minmax_goal():
                    f = self._msat_lib.msat_make_minmax
                else:
                    f = self._msat_lib.msat_make_maxmin

                cost_function = g.terms
                obj_fun = []
                for f in cost_function:
                    obj_fun.append(self.converter.convert(f))
            elif g.is_minimization_goal() or g.is_maximization_goal():
                if g.is_minimization_goal():
                    f = self._msat_lib.msat_make_minimize
                else:
                    f = self._msat_lib.msat_make_maximize

                cost_function = g.term()
                obj_fun = self.converter.convert(cost_function)
            else:
                raise GoalNotSupportedError("optimathsat", g)

            msat_obj = f(self.msat_env(), obj_fun)
            msat_objs.append(msat_obj)
            self._msat_lib.msat_assert_objective(self.msat_env(), msat_obj)
        rt = self.solve()
        if rt:
            model = self.get_model()
            return model, [model.get_value(x.term()) for x in goals]
        else:
            return None, None

    def boxed_optimization(self, goals):
        self._msat_lib.msat_set_opt_priority(self.environment, "box")
        msat_objs = []

        for g in goals:
            if g.is_minmax_goal() or g.is_maxmin_goal():
                if g.is_minmax_goal():
                    f = self._msat_lib.msat_make_minmax
                else:
                    f = self._msat_lib.msat_make_maxmin

                cost_function = g.terms
                obj_fun = []
                for f in cost_function:
                    obj_fun.append(self.converter.convert(f))
            elif g.is_minimization_goal() or g.is_maximization_goal():
                if g.is_minimization_goal():
                    f = self._msat_lib.msat_make_minimize
                else:
                    f = self._msat_lib.msat_make_maximize

                cost_function = g.term()
                obj_fun = self.converter.convert(cost_function)
            else:
                raise GoalNotSupportedError("optimathsat", g)

            msat_obj = f(self.msat_env(), obj_fun)
            msat_objs.append(msat_obj)
            self._msat_lib.msat_assert_objective(self.msat_env(), msat_obj)
        rt = self.solve()
        model = self.get_model()
        #print("model in name " + model.__class__.__name__)
        #print("rt in " + rt.__class__.__name__)
        while(rt):
            #print("model in name " + model.__class__.__name__)
            yield model
            rt = self.solve()
            #print("rt " + str(rt))
            model = self.get_model()

        #print("end")
        return None


    def get_model(self):
        return OptiMSATModel(self.environment, self.msat_env)


    def can_diverge_for_unbounded_cases(self):
        return False


class OptiMSATConverter(MSatConverter):
    __lib_name__ = "optimathsat"

    def __init__(self, environment, msat_env):
        MSatConverter.__init__(self, environment=environment,
                               msat_env=msat_env)

    def _get_bool_uf_rewriter(self, environment):
        return OptiMSATBoolUFRewriter(environment=environment)


class OptiMSATQuantifierEliminator(MSatQuantifierEliminator):
    __lib_name__ = "optimathsat"

    LOGICS = MSatQuantifierEliminator.LOGICS

    def __init__(self, environment, logic=None, algorithm='lw'):
        MSatQuantifierEliminator.__init__(self, environment=environment,
                                          logic=logic, algorithm=algorithm)


class OptiMSATFMQuantifierEliminator(OptiMSATQuantifierEliminator):
    LOGICS = [LRA]

    def __init__(self, environment, logic=None):
        OptiMSATQuantifierEliminator.__init__(self, environment,
                                              logic=logic, algorithm='fm')


class OptiMSATLWQuantifierEliminator(OptiMSATQuantifierEliminator):
    LOGICS = [LRA, LIA]

    def __init__(self, environment, logic=None):
        OptiMSATQuantifierEliminator.__init__(self, environment,
                                              logic=logic, algorithm='lw')


class OptiMSATInterpolator(MSatInterpolator):
    __lib_name__ = "optimathsat"
    LOGICS = MSatInterpolator.LOGICS

    def __init__(self, environment, logic=None):
        MSatInterpolator.__init__(self, environment=environment,
                                  logic=logic)

    def _do_create_env(self, msat_config=None, msat_env=None):
        return self._msat_lib.msat_create_opt_env(msat_config, msat_env)

    def _do_create_env(self, msat_config=None, msat_env=None):
        return self._msat_lib.msat_create_opt_env(msat_config, msat_env)


class OptiMSATBoolUFRewriter(MSatBoolUFRewriter):
    __lib_name__ = "optimathsat"

    def __init__(self, environment):
        MSatBoolUFRewriter.__init__(self, environment=environment)


class OptiMSATSUAOptimizer(OptiMSATSolver, SUAOptimizerMixin):
    LOGICS = OptiMSATSolver.LOGICS


class OptiMSATIncrementalOptimizer(OptiMSATSolver, IncrementalOptimizerMixin):
    LOGICS = OptiMSATSolver.LOGICS

